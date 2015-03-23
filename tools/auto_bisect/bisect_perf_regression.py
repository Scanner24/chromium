# Copyright 2013 The Chromium Authors. All rights reserved.
"""Chromium auto-bisect tool

This script bisects a range of commits using binary search. It starts by getting
reference values for the specified "good" and "bad" commits. Then, for revisions
in between, it will get builds, run tests and classify intermediate revisions as
"good" or "bad" until an adjacent "good" and "bad" revision is found; this is
the culprit.

If the culprit is a roll of a depedency repository (e.g. v8), it will then
expand the revision range and continue the bisect until a culprit revision in
the dependency repository is found.

Example usage using git commit hashes, bisecting a performance test based on
the mean value of a particular metric:

./tools/auto_bisect/bisect_perf_regression.py
  --command "out/Release/performance_ui_tests \
      --gtest_filter=ShutdownTest.SimpleUserQuit"\
  --metric shutdown/simple-user-quit
  --good_revision 1f6e67861535121c5c819c16a666f2436c207e7b\
  --bad-revision b732f23b4f81c382db0b23b9035f3dadc7d925bb\

Example usage using git commit positions, bisecting a functional test based on
whether it passes or fails.

./tools/auto_bisect/bisect_perf_regression.py\
  --command "out/Release/content_unittests -single-process-tests \
            --gtest_filter=GpuMemoryBufferImplTests"\
  --good_revision 408222\
  --bad_revision 408232\
  --bisect_mode return_code\
  --builder_type full

In practice, the auto-bisect tool is usually run on tryserver.chromium.perf
try bots, and is started by tools/run-bisect-perf-regression.py using
config parameters from tools/auto_bisect/bisect.cfg.
import logging
import argparse
from bisect_printer import BisectPrinter
from bisect_state import BisectState
import fetch_build
import query_crbug
import source_control
# The confidence percentage we require to consider the initial range a
# regression based on the test results of the initial good and bad revisions.
REGRESSION_CONFIDENCE = 80
# How many times to repeat the test on the last known good and first known bad
# revisions in order to assess a more accurate confidence score in the
# regression culprit.
BORDER_REVISIONS_EXTRA_RUNS = 2
DEPS_SHA_PATCH = """diff --git DEPS.sha DEPS.sha
+++ DEPS.sha
REGRESSION_CONFIDENCE_ERROR_TEMPLATE = """
We could not reproduce the regression with this test/metric/platform combination
with enough confidence.
Here are the results for the given "good" and "bad" revisions:
"Good" revision: {good_rev}
\tMean: {good_mean}
\tStandard error: {good_std_err}
\tSample size: {good_sample_size}
"Bad" revision: {bad_rev}
\tMean: {bad_mean}
\tStandard error: {bad_std_err}
\tSample size: {bad_sample_size}
NOTE: There's still a chance that this is actually a regression, but you may
      need to bisect a different platform."""
# Git branch name used to run bisect try jobs.
BISECT_TRYJOB_BRANCH = 'bisect-tryjob'
# Git master branch name.
BISECT_MASTER_BRANCH = 'master'
# File to store 'git diff' content.
BISECT_PATCH_FILE = 'deps_patch.txt'
# SVN repo where the bisect try jobs are submitted.
PERF_SVN_REPO_URL = 'svn://svn.chromium.org/chrome-try/try-perf'
FULL_SVN_REPO_URL = 'svn://svn.chromium.org/chrome-try/try'
class RunGitError(Exception):
  def __str__(self):
    return '%s\nError executing git command.' % self.args[0]
def GetSHA1HexDigest(contents):
  """Returns SHA1 hex digest of the given string."""
  return hashlib.sha1(contents).hexdigest()
    raise RuntimeError('Error writing to file [%s]' % file_name)
    raise RuntimeError('Error reading file [%s]' % file_name)
  rxp = re.compile(r"'(?P<depot_body>[\w_-]+)':[\s]+'(?P<rev_body>[\w@]+)'",
def _WaitUntilBuildIsReady(fetch_build_func, builder_name, builder_type,
                           build_request_id, max_timeout):
    fetch_build_func: Function to check and download build from cloud storage.
    builder_name: Builder bot name on try server.
    builder_type: Builder type, e.g. "perf" or "full". Refer to the constants
        |fetch_build| which determine the valid values that can be passed.
    res = fetch_build_func()
            build_request_id, builder_name, builder_type)
          build_num, builder_name, builder_type)
    logging.info('Time elapsed: %ss without build.', elapsed_time)
  deps_var = bisect_utils.DEPOT_DEPS_NAME[depot]['deps_var']
    match = re.search(angle_rev_pattern, deps_contents)
        logging.info('Could not find angle revision information in DEPS file.')
    logging.warn('Something went wrong while updating DEPS file, %s', e)
      metric_re + r'\s*(?P<VALUE>[-]?\d*(\.\d*)?)')
      metric_re + r'\s*\[\s*(?P<VALUES>[-]?[\d\., ]+)\s*\]')
      r'\s*\{\s*(?P<MEAN>[-]?\d*(\.\d*)?),\s*(?P<STDDEV>\d+(\.\d*)?)\s*\}')
def _CheckRegressionConfidenceError(
    good_revision,
    bad_revision,
    known_good_value,
    known_bad_value):
  """Checks whether we can be confident beyond a certain degree that the given
  metrics represent a regression.
    good_revision: string representing the commit considered 'good'
    bad_revision: Same as above for 'bad'.
    known_good_value: A dict with at least: 'values', 'mean' and 'std_err'
    known_bad_value: Same as above.
  Returns:
    False if there is no error (i.e. we can be confident there's a regressioni),
    a string containing the details of the lack of confidence otherwise.
  error = False
  # Adding good and bad values to a parameter list.
  confidence_params = []
  for l in [known_bad_value['values'], known_good_value['values']]:
    # Flatten if needed, by averaging the values in each nested list
    if isinstance(l, list) and all([isinstance(x, list) for x in l]):
      averages = map(math_utils.Mean, l)
      confidence_params.append(averages)
    else:
      confidence_params.append(l)
  regression_confidence = BisectResults.ConfidenceScore(*confidence_params)
  if regression_confidence < REGRESSION_CONFIDENCE:
    error = REGRESSION_CONFIDENCE_ERROR_TEMPLATE.format(
        good_rev=good_revision,
        good_mean=known_good_value['mean'],
        good_std_err=known_good_value['std_err'],
        good_sample_size=len(known_good_value['values']),
        bad_rev=bad_revision,
        bad_mean=known_bad_value['mean'],
        bad_std_err=known_bad_value['std_err'],
        bad_sample_size=len(known_bad_value['values']))
  return error
    for depot in bisect_utils.DEPOT_NAMES:
      path_in_src = bisect_utils.DEPOT_DEPS_NAME[depot]['src'][4:]
      self.SetDepotDir(depot, os.path.join(src_cwd, path_in_src))
    self.SetDepotDir('chromium', src_cwd)
  def SetDepotDir(self, depot_name, depot_dir):
def _PrepareBisectBranch(parent_branch, new_branch):
  """Creates a new branch to submit bisect try job.

  Args:
    parent_branch: Parent branch to be used to create new branch.
    new_branch: New branch name.
  """
  current_branch, returncode = bisect_utils.RunGit(
      ['rev-parse', '--abbrev-ref', 'HEAD'])
  if returncode:
    raise RunGitError('Must be in a git repository to send changes to trybots.')

  current_branch = current_branch.strip()
  # Make sure current branch is master.
  if current_branch != parent_branch:
    output, returncode = bisect_utils.RunGit(['checkout', '-f', parent_branch])
    if returncode:
      raise RunGitError('Failed to checkout branch: %s.' % output)

  # Delete new branch if exists.
  output, returncode = bisect_utils.RunGit(['branch', '--list'])
  if new_branch in output:
    output, returncode = bisect_utils.RunGit(['branch', '-D', new_branch])
    if returncode:
      raise RunGitError('Deleting branch failed, %s', output)

  # Check if the tree is dirty: make sure the index is up to date and then
  # run diff-index.
  bisect_utils.RunGit(['update-index', '--refresh', '-q'])
  output, returncode = bisect_utils.RunGit(['diff-index', 'HEAD'])
  if output:
    raise RunGitError('Cannot send a try job with a dirty tree.')

  # Create/check out the telemetry-tryjob branch, and edit the configs
  # for the tryjob there.
  output, returncode = bisect_utils.RunGit(['checkout', '-b', new_branch])
  if returncode:
    raise RunGitError('Failed to checkout branch: %s.' % output)

  output, returncode = bisect_utils.RunGit(
      ['branch', '--set-upstream-to', parent_branch])
  if returncode:
    raise RunGitError('Error in git branch --set-upstream-to')


def _StartBuilderTryJob(
    builder_type, git_revision, builder_name, job_name, patch=None):
  """Attempts to run a try job from the current directory.

  Args:
    builder_type: One of the builder types in fetch_build, e.g. "perf".
    git_revision: A git commit hash.
    builder_name: Name of the bisect bot to be used for try job.
    bisect_job_name: Try job name, used to identify which bisect
        job was responsible for requesting a build.
    patch: A DEPS patch (used while bisecting dependency repositories),
        or None if we're bisecting the top-level repository.
  """
  # TODO(prasadv, qyearsley): Make this a method of BuildArchive
  # (which may be renamed to BuilderTryBot or Builder).
  try:
    # Temporary branch for running tryjob.
    _PrepareBisectBranch(BISECT_MASTER_BRANCH, BISECT_TRYJOB_BRANCH)
    patch_content = '/dev/null'
    # Create a temporary patch file.
    if patch:
      WriteStringToFile(patch, BISECT_PATCH_FILE)
      patch_content = BISECT_PATCH_FILE

    try_command = [
        'try',
        '--bot=%s' % builder_name,
        '--revision=%s' % git_revision,
        '--name=%s' % job_name,
        '--svn_repo=%s' % _TryJobSvnRepo(builder_type),
        '--diff=%s' % patch_content,
    ]
    # Execute try job to build revision.
    print try_command
    output, return_code = bisect_utils.RunGit(try_command)

    command_string = ' '.join(['git'] + try_command)
    if return_code:
      raise RunGitError('Could not execute tryjob: %s.\n'
                        'Error: %s' % (command_string, output))
    logging.info('Try job successfully submitted.\n TryJob Details: %s\n%s',
                 command_string, output)
  finally:
    # Delete patch file if exists.
    try:
      os.remove(BISECT_PATCH_FILE)
    except OSError as e:
      if e.errno != errno.ENOENT:
        raise
    # Checkout master branch and delete bisect-tryjob branch.
    bisect_utils.RunGit(['checkout', '-f', BISECT_MASTER_BRANCH])
    bisect_utils.RunGit(['branch', '-D', BISECT_TRYJOB_BRANCH])


def _TryJobSvnRepo(builder_type):
  """Returns an SVN repo to use for try jobs based on the builder type."""
  if builder_type == fetch_build.PERF_BUILDER:
    return PERF_SVN_REPO_URL
  if builder_type == fetch_build.FULL_BUILDER:
    return FULL_SVN_REPO_URL
  raise NotImplementedError('Unknown builder type "%s".' % builder_type)


  def __init__(self, opts, src_cwd):
    """Constructs a BisectPerformancesMetrics object.

    Args:
      opts: BisectOptions object containing parsed options.
      src_cwd: Root src/ directory of the test repository (inside bisect/ dir).
    """
    self.src_cwd = src_cwd
    self.printer = BisectPrinter(self.opts, self.depot_registry)
    cwd = self.depot_registry.GetDepotDir(depot)
    return source_control.GetRevisionList(bad_revision, good_revision, cwd=cwd)
      for depot_name, depot_data in bisect_utils.DEPOT_DEPS_NAME.iteritems():
        if depot_data.get('recurse') and depot in depot_data.get('from'):
            self.depot_registry.SetDepotDir(depot_name, os.path.join(
        logging.warn(depot_name, depot_revision)
        for cur_name, cur_data in bisect_utils.DEPOT_DEPS_NAME.iteritems():
          if (cur_data.has_key('deps_var') and
              cur_data['deps_var'] == depot_name):
            src_name = cur_name
      RemoveDirectoryTree(destination_dir)
  def _DownloadAndUnzipBuild(self, revision, depot, build_type='Release',
                             create_patch=False):
      revision: The git revision to download.
      depot: The name of a dependency repository. Should be in DEPOT_NAMES.
      build_type: Target build type, e.g. Release', 'Debug', 'Release_x64' etc.
      create_patch: Create a patch with any locally modified files.
    patch = None
    if depot != 'chromium':
      # Create a DEPS patch with new revision for dependency repository.
      self._CreateDEPSPatch(depot, revision)
      create_patch = True

    if create_patch:
      revision, patch = self._CreatePatch(revision)

    build_dir = builder.GetBuildOutputDirectory(self.opts, self.src_cwd)
    downloaded_file = self._WaitForBuildDownload(
        revision, build_dir, deps_patch=patch, deps_patch_sha=patch_sha)
    if not downloaded_file:
      return False
    return self._UnzipAndMoveBuildProducts(downloaded_file, build_dir,
                                           build_type=build_type)
  def _WaitForBuildDownload(self, revision, build_dir, deps_patch=None,
                            deps_patch_sha=None):
    """Tries to download a zip archive for a build.
    This involves seeing whether the archive is already available, and if not,
    then requesting a build and waiting before downloading.
    Args:
      revision: A git commit hash.
      build_dir: The directory to download the build into.
      deps_patch: A patch which changes a dependency repository revision in
          the DEPS, if applicable.
      deps_patch_sha: The SHA1 hex digest of the above patch.

    Returns:
      File path of the downloaded file if successful, otherwise None.
    """
    bucket_name, remote_path = fetch_build.GetBucketAndRemotePath(
        revision, builder_type=self.opts.builder_type,
        target_arch=self.opts.target_arch,
        target_platform=self.opts.target_platform,
        deps_patch_sha=deps_patch_sha)
    output_dir = os.path.abspath(build_dir)
    fetch_build_func = lambda: fetch_build.FetchFromCloudStorage(
        bucket_name, remote_path, output_dir)

    is_available = fetch_build.BuildIsAvailable(bucket_name, remote_path)
    if is_available:
      return fetch_build_func()

    # When build archive doesn't exist, make a request and wait.
    return self._RequestBuildAndWait(
        revision, fetch_build_func, deps_patch=deps_patch)

  def _RequestBuildAndWait(self, git_revision, fetch_build_func,
                           deps_patch=None):
    """Triggers a try job for a build job.

    This function prepares and starts a try job for a builder, and waits for
    the archive to be produced and archived. Once the build is ready it is
    downloaded.

    For performance tests, builders on the tryserver.chromium.perf are used.

    TODO(qyearsley): Make this function take "builder_type" as a parameter
    and make requests to different bot names based on that parameter.

    Args:
      git_revision: A git commit hash.
      fetch_build_func: Function to check and download build from cloud storage.
      deps_patch: DEPS patch string, used when bisecting dependency repos.

    Returns:
      Downloaded archive file path when requested build exists and download is
      successful, otherwise None.
    """
    if not fetch_build_func:
      return None

    # Create a unique ID for each build request posted to try server builders.
    # This ID is added to "Reason" property of the build.
    build_request_id = GetSHA1HexDigest(
        '%s-%s-%s' % (git_revision, deps_patch, time.time()))

    # Revert any changes to DEPS file.
    bisect_utils.CheckRunGit(['reset', '--hard', 'HEAD'], cwd=self.src_cwd)

    builder_name, build_timeout = fetch_build.GetBuilderNameAndBuildTime(
        builder_type=self.opts.builder_type,
        target_arch=self.opts.target_arch,
        target_platform=self.opts.target_platform)

    try:
      _StartBuilderTryJob(self.opts.builder_type, git_revision, builder_name,
                          job_name=build_request_id, patch=deps_patch)
    except RunGitError as e:
      logging.warn('Failed to post builder try job for revision: [%s].\n'
                   'Error: %s', git_revision, e)
      return None

    archive_filename, error_msg = _WaitUntilBuildIsReady(
        fetch_build_func, builder_name, self.opts.builder_type,
        build_request_id, build_timeout)
    if not archive_filename:
      logging.warn('%s [revision: %s]', error_msg, git_revision)
    return archive_filename

  def _UnzipAndMoveBuildProducts(self, downloaded_file, build_dir,
                                 build_type='Release'):
    """Unzips the build archive and moves it to the build output directory.

    The build output directory is wherever the binaries are expected to
    be in order to start Chrome and run tests.

    TODO: Simplify and clarify this method if possible.

    Args:
      downloaded_file: File path of the downloaded zip file.
      build_dir: Directory where the the zip file was downloaded to.
      build_type: "Release" or "Debug".

    Returns:
      True if successful, False otherwise.
    """
    abs_build_dir = os.path.abspath(build_dir)
    output_dir = os.path.join(abs_build_dir, self.GetZipFileBuildDirName())
    logging.info('EXPERIMENTAL RUN, _UnzipAndMoveBuildProducts locals %s',
                 str(locals()))
      RemoveDirectoryTree(output_dir)

      logging.info('Extracting "%s" to "%s"', downloaded_file, abs_build_dir)
      fetch_build.Unzip(downloaded_file, abs_build_dir)

      logging.info('Moving build from %s to %s',
                   output_dir, target_build_output_dir)
      logging.info('Something went wrong while extracting archive file: %s', e)
        RemoveDirectoryTree(output_dir)
  @staticmethod
  def GetZipFileBuildDirName():
    """Gets the base file name of the zip file.
    After extracting the zip file, this is the name of the directory where
    the build files are expected to be. Possibly.
    TODO: Make sure that this returns the actual directory name where the
    Release or Debug directory is inside of the zip files. This probably
    depends on the builder recipe, and may depend on whether the builder is
    a perf builder or full builder.
      The name of the directory inside a build archive which is expected to
      contain a Release or Debug directory.
    if bisect_utils.IsWindowsHost():
      return 'full-build-win32'
    if bisect_utils.IsLinuxHost():
      return 'full-build-linux'
    if bisect_utils.IsMacHost():
      return 'full-build-mac'
    raise NotImplementedError('Unknown platform "%s".' % sys.platform)
    if (self.opts.target_platform in ['chromium', 'android']
        and self.opts.builder_type):
              'chromium' in bisect_utils.DEPOT_DEPS_NAME[depot]['from'] or
              'v8' in bisect_utils.DEPOT_DEPS_NAME[depot]['from'])
      commit_position = source_control.GetCommitPosition(
        logging.warn('Could not determine commit position for %s', git_revision)
    deps_var = bisect_utils.DEPOT_DEPS_NAME[depot]['deps_var']
      logging.warn('DEPS update not supported for Depot: %s', depot)
      logging.warn('Something went wrong while updating DEPS file. [%s]', e)
  def _CreateDEPSPatch(self, depot, revision):
    """Checks out the DEPS file at the specified revision and modifies it.
    if ('chromium' in bisect_utils.DEPOT_DEPS_NAME[depot]['from'] or
        'v8' in bisect_utils.DEPOT_DEPS_NAME[depot]['from']):
      if not source_control.CheckoutFileAtRevision(
      if not self.UpdateDeps(revision, depot, deps_file_path):
        raise RuntimeError(
            'Failed to update DEPS file for chromium: [%s]' % chromium_sha)

  def _CreatePatch(self, revision):
    """Creates a patch from currently modified files.

    Args:
      depot: Current depot being bisected.
      revision: A git hash revision of the dependency repository.
      A tuple with git hash of chromium revision and DEPS patch text.
    # Get current chromium revision (git hash).
    chromium_sha = bisect_utils.CheckRunGit(['rev-parse', 'HEAD']).strip()
    if not chromium_sha:
      raise RuntimeError('Failed to determine Chromium revision for %s' %
                         revision)
    # Checkout DEPS file for the current chromium revision.
    diff_command = [
        'diff',
        '--src-prefix=',
        '--dst-prefix=',
        '--no-ext-diff',
        'HEAD',
    ]
    diff_text = bisect_utils.CheckRunGit(diff_command)
    return (chromium_sha, ChangeBackslashToSlashInPatch(diff_text))

  def ObtainBuild(
      self, depot, revision=None, create_patch=False):
    """Obtains a build by either downloading or building directly.
    Args:
      depot: Dependency repository name.
      revision: A git commit hash. If None is given, the currently checked-out
          revision is built.
      create_patch: Create a patch with any locally modified files.

    Returns:
      True for success.
    """
    if self.opts.debug_ignore_build:
      return True

    build_success = False
      build_success = self._DownloadAndUnzipBuild(
          revision, depot, build_type='Release', create_patch=create_patch)
      # Build locally.
    return self.opts.bisect_mode in [bisect_utils.BISECT_MODE_MEAN,
                                     bisect_utils.BISECT_MODE_STD_DEV]
    return self.opts.bisect_mode in [bisect_utils.BISECT_MODE_RETURN_CODE]
    return self.opts.bisect_mode in [bisect_utils.BISECT_MODE_STD_DEV]
      commit_position = source_control.GetCommitPosition(revision,
                                                         cwd=self.src_cwd)
      cmd_re = re.compile(r'--browser=(?P<browser_type>\S+)')
      upload_on_last_run=False, results_label=None, test_run_multiplier=1):
      test_run_multiplier: Factor by which to multiply the number of test runs
          and the timeout period specified in self.opts.

      # When debug_fake_test_mean is set, its value is returned as the mean
      # and the flag is cleared so that further calls behave as if it wasn't
      # set (returning the fake_results dict as defined above).
      if self.opts.debug_fake_first_test_mean:
        fake_results['mean'] = float(self.opts.debug_fake_first_test_mean)
        self.opts.debug_fake_first_test_mean = 0

    repeat_count = self.opts.repeat_test_count * test_run_multiplier
    for i in xrange(repeat_count):
        if i == self.opts.repeat_test_count - 1 and upload_on_last_run:
          err_text = ('Something went wrong running the performance test. '
                      'Please review the command line:\n\n')
        parsed_metric = _ParseMetricValuesFromOutput(metric, output)
        if parsed_metric:
          metric_values.append(math_utils.Mean(parsed_metric))
      time_limit = self.opts.max_time_minutes *  test_run_multiplier
      if elapsed_minutes >= time_limit:
  def _RunPostSync(self, _depot):
    if 'android' in self.opts.target_platform:
    return self.RunGClientHooks()
  @staticmethod
  def ShouldSkipRevision(depot, revision):
    Some commits can be safely skipped (such as a DEPS roll for the repos
    still using .DEPS.git), since the tool is git based those changes
    would have no effect.
    # Skips revisions with DEPS on android-chrome.
    if depot == 'android-chrome':
      cmd = ['diff-tree', '--no-commit-id', '--name-only', '-r', revision]
      output = bisect_utils.CheckRunGit(cmd)
      files = output.splitlines()
      if len(files) == 1 and files[0] == 'DEPS':
        return True
  def RunTest(self, revision, depot, command, metric, skippable=False,
              skip_sync=False, create_patch=False, force_build=False,
              test_run_multiplier=1):
      skip_sync: Skip the sync step.
      create_patch: Create a patch with any locally modified files.
      force_build: Force a local build.
      test_run_multiplier: Factor by which to multiply the given number of runs
          and the set timeout period.
    logging.info('Running RunTest with rev "%s", command "%s"',
                 revision, command)
    if not (self.opts.debug_ignore_sync or skip_sync):
      if not self._SyncRevision(depot, revision, sync_client):
    # Try to do any post-sync steps. This may include "gclient runhooks".
    revision_to_build = revision if not force_build else None
    build_success = self.ObtainBuild(
        depot, revision=revision_to_build, create_patch=create_patch)
    results = self.RunPerformanceTestAndParseResults(
        command, metric, test_run_multiplier=test_run_multiplier)
  def _SyncRevision(self, depot, revision, sync_client):
    """Syncs depot to particular revision.
      depot: The depot that's being used at the moment (src, webkit, etc.)
      revision: The revision to sync to.
      sync_client: Program used to sync, e.g. "gclient". Can be None.
    self.depot_registry.ChangeToDepotDir(depot)
    if sync_client:
      self.PerformPreBuildCleanup()
    # When using gclient to sync, you need to specify the depot you
    # want so that all the dependencies sync properly as well.
    # i.e. gclient sync src@<SHA1>
    if sync_client == 'gclient' and revision:
      revision = '%s@%s' % (bisect_utils.DEPOT_DEPS_NAME[depot]['src'],
          revision)
      if depot == 'chromium' and self.opts.target_platform == 'android-chrome':
        return self._SyncRevisionsForAndroidChrome(revision)
    return source_control.SyncToRevision(revision, sync_client)
  def _SyncRevisionsForAndroidChrome(self, revision):
    """Syncs android-chrome and chromium repos to particular revision.

    This is a special case for android-chrome as the gclient sync for chromium
    overwrites the android-chrome revision to TOT. Therefore both the repos
    are synced to known revisions.

    Args:
      revision: Git hash of the Chromium to sync.

    Returns:
      True if successful, False otherwise.
    """
    revisions_list = [revision]
    current_android_rev = source_control.GetCurrentRevision(
        self.depot_registry.GetDepotDir('android-chrome'))
    revisions_list.append(
        '%s@%s' % (bisect_utils.DEPOT_DEPS_NAME['android-chrome']['src'],
                   current_android_rev))
    return not bisect_utils.RunGClientAndSync(revisions_list)
    if self.opts.bisect_mode == bisect_utils.BISECT_MODE_STD_DEV:
  def _GetV8BleedingEdgeFromV8TrunkIfMappable(
      self, revision, bleeding_edge_branch):
    """Gets v8 bleeding edge revision mapped to v8 revision in trunk.

    Args:
      revision: A trunk V8 revision mapped to bleeding edge revision.
      bleeding_edge_branch: Branch used to perform lookup of bleeding edge
                            revision.
    Return:
      A mapped bleeding edge revision if found, otherwise None.
    """
    commit_position = source_control.GetCommitPosition(revision)

    if bisect_utils.IsStringInt(commit_position):
      # V8 is tricky to bisect, in that there are only a few instances when
      # we can dive into bleeding_edge and get back a meaningful result.
      # Try to detect a V8 "business as usual" case, which is when:
      #  1. trunk revision N has description "Version X.Y.Z"
      #  2. bleeding_edge revision (N-1) has description "Prepare push to
      #     trunk. Now working on X.Y.(Z+1)."
      #
      # As of 01/24/2014, V8 trunk descriptions are formatted:
      # "Version 3.X.Y (based on bleeding_edge revision rZ)"
      # So we can just try parsing that out first and fall back to the old way.
      v8_dir = self.depot_registry.GetDepotDir('v8')
      v8_bleeding_edge_dir = self.depot_registry.GetDepotDir('v8_bleeding_edge')

      revision_info = source_control.QueryRevisionInfo(revision, cwd=v8_dir)
      version_re = re.compile("Version (?P<values>[0-9,.]+)")
      regex_results = version_re.search(revision_info['subject'])
      if regex_results:
        git_revision = None
        if 'based on bleeding_edge' in revision_info['subject']:
          try:
            bleeding_edge_revision = revision_info['subject'].split(
                'bleeding_edge revision r')[1]
            bleeding_edge_revision = int(bleeding_edge_revision.split(')')[0])
            bleeding_edge_url = ('https://v8.googlecode.com/svn/branches/'
                                 'bleeding_edge@%s' % bleeding_edge_revision)
            cmd = ['log',
                   '--format=%H',
                   '--grep',
                   bleeding_edge_url,
                   '-1',
                   bleeding_edge_branch]
            output = bisect_utils.CheckRunGit(cmd, cwd=v8_dir)
            if output:
              git_revision = output.strip()
            return git_revision
          except (IndexError, ValueError):
            pass
        else:
          # V8 rolls description changed after V8 git migration, new description
          # includes "Version 3.X.Y (based on <git hash>)"
          try:
            rxp = re.compile('based on (?P<git_revision>[a-fA-F0-9]+)')
            re_results = rxp.search(revision_info['subject'])
            if re_results:
              return re_results.group('git_revision')
          except (IndexError, ValueError):
            pass
        if not git_revision:
          # Wasn't successful, try the old way of looking for "Prepare push to"
          git_revision = source_control.ResolveToRevision(
              int(commit_position) - 1, 'v8_bleeding_edge',
              bisect_utils.DEPOT_DEPS_NAME, -1, cwd=v8_bleeding_edge_dir)

          if git_revision:
            revision_info = source_control.QueryRevisionInfo(git_revision,
                cwd=v8_bleeding_edge_dir)

            if 'Prepare push to trunk' in revision_info['subject']:
              return git_revision
    return None

  def _GetNearestV8BleedingEdgeFromTrunk(
      self, revision, v8_branch, bleeding_edge_branch, search_forward=True):
    """Gets the nearest V8 roll and maps to bleeding edge revision.

    V8 is a bit tricky to bisect since it isn't just rolled out like blink.
    Each revision on trunk might just be whatever was in bleeding edge, rolled
    directly out. Or it could be some mixture of previous v8 trunk versions,
    with bits and pieces cherry picked out from bleeding edge. In order to
    bisect, we need both the before/after versions on trunk v8 to be just pushes
    from bleeding edge. With the V8 git migration, the branches got switched.
    a) master (external/v8) == candidates (v8/v8)
    b) bleeding_edge (external/v8) == master (v8/v8)

    Args:
      revision: A V8 revision to get its nearest bleeding edge revision
      search_forward: Searches forward if True, otherwise search backward.

    Return:
      A mapped bleeding edge revision if found, otherwise None.
    """
    cwd = self.depot_registry.GetDepotDir('v8')
    cmd = ['log', '--format=%ct', '-1', revision]
    output = bisect_utils.CheckRunGit(cmd, cwd=cwd)
    commit_time = int(output)
    commits = []
    if search_forward:
      cmd = ['log',
             '--format=%H',
             '--after=%d' % commit_time,
             v8_branch,
             '--reverse']
      output = bisect_utils.CheckRunGit(cmd, cwd=cwd)
      output = output.split()
      commits = output
      #Get 10 git hashes immediately after the given commit.
      commits = commits[:10]
    else:
      cmd = ['log',
             '--format=%H',
             '-10',
             '--before=%d' % commit_time,
             v8_branch]
      output = bisect_utils.CheckRunGit(cmd, cwd=cwd)
      output = output.split()
      commits = output

    bleeding_edge_revision = None

    for c in commits:
      bleeding_edge_revision = self._GetV8BleedingEdgeFromV8TrunkIfMappable(
          c, bleeding_edge_branch)
      if bleeding_edge_revision:
        break

    return bleeding_edge_revision

  def _FillInV8BleedingEdgeInfo(self, min_revision_state, max_revision_state):
    cwd = self.depot_registry.GetDepotDir('v8')
    # when "remote.origin.url" is https://chromium.googlesource.com/v8/v8.git
    v8_branch = 'origin/candidates'
    bleeding_edge_branch = 'origin/master'

    # Support for the chromium revisions with external V8 repo.
    # ie https://chromium.googlesource.com/external/v8.git
    cmd = ['config', '--get', 'remote.origin.url']
    v8_repo_url = bisect_utils.CheckRunGit(cmd, cwd=cwd)

    if 'external/v8.git' in v8_repo_url:
      v8_branch = 'origin/master'
      bleeding_edge_branch = 'origin/bleeding_edge'

    r1 = self._GetNearestV8BleedingEdgeFromTrunk(min_revision_state.revision,
        v8_branch, bleeding_edge_branch, search_forward=True)
    r2 = self._GetNearestV8BleedingEdgeFromTrunk(max_revision_state.revision,
        v8_branch, bleeding_edge_branch, search_forward=False)
    min_revision_state.external['v8_bleeding_edge'] = r1
    max_revision_state.external['v8_bleeding_edge'] = r2
            min_revision_state.revision, bleeding_edge_branch)
            max_revision_state.revision, bleeding_edge_branch)):
      self, current_depot, min_revision_state, max_revision_state):
      min_revision_state: State of the earliest revision in the bisect range.
      max_revision_state: State of the latest revision in the bisect range.
    for next_depot in bisect_utils.DEPOT_NAMES:
      if bisect_utils.DEPOT_DEPS_NAME[next_depot].has_key('platform'):
        if bisect_utils.DEPOT_DEPS_NAME[next_depot]['platform'] != os.name:
      if not (bisect_utils.DEPOT_DEPS_NAME[next_depot]['recurse']
              and min_revision_state.depot
              in bisect_utils.DEPOT_DEPS_NAME[next_depot]['from']):
        self._FillInV8BleedingEdgeInfo(min_revision_state, max_revision_state)
      if (min_revision_state.external.get(next_depot) ==
          max_revision_state.external.get(next_depot)):
      if (min_revision_state.external.get(next_depot) and
          max_revision_state.external.get(next_depot)):
      self, current_depot, start_revision, end_revision, previous_revision):
      end_revision: End of the revision range.
    if bisect_utils.DEPOT_DEPS_NAME[current_depot].has_key('custom_deps'):
      if bisect_utils.RunGClientAndCreateConfig(
          self.opts, bisect_utils.DEPOT_DEPS_NAME[current_depot]['custom_deps'],
          cwd=config_path):
      self.depot_registry.SetDepotDir('v8_bleeding_edge',
      self.depot_registry.SetDepotDir('v8', os.path.join(self.src_cwd,
                                                         'v8.bak'))
      step_name = 'Bisection Range: [%s:%s - %s]' % (depot, revision_list[-1],
                                                     revision_list[0])
    if self.opts.target_platform == 'chromium':
      changes_to_deps = source_control.QueryFileRevisionHistory(
        changes_to_gitdeps = source_control.QueryFileRevisionHistory(
    cwd = self.depot_registry.GetDepotDir(target_depot)
    good_position = source_control.GetCommitPosition(good_revision, cwd)
    bad_position = source_control.GetCommitPosition(bad_revision, cwd)
    # Compare commit timestamp for repos that don't support commit position.
    if not (bad_position and good_position):
      good_position = source_control.GetCommitTime(good_revision, cwd=cwd)
      bad_position = source_control.GetCommitTime(bad_revision, cwd=cwd)
    return good_position <= bad_position
      good_revision = source_control.GetCommitPosition(good_revision)
      good_revision = source_control.GetCommitPosition(good_revision)
      bad_revision = source_control.GetCommitPosition(bad_revision)
  def _GatherResultsFromRevertedCulpritCL(
      self, results, target_depot, command_to_run, metric):
    """Gathers performance results with/without culprit CL.

    Attempts to revert the culprit CL against ToT and runs the
    performance tests again with and without the CL, adding the results to
    the over bisect results.

    Args:
      results: BisectResults from the bisect.
      target_depot: The target depot we're bisecting.
      command_to_run: Specify the command to execute the performance test.
      metric: The performance metric to monitor.
    """
    run_results_tot, run_results_reverted = self._RevertCulpritCLAndRetest(
      results, target_depot, command_to_run, metric)

    results.AddRetestResults(run_results_tot, run_results_reverted)

    if len(results.culprit_revisions) != 1:
      return

    # Cleanup reverted files if anything is left.
    _, _, culprit_depot = results.culprit_revisions[0]
    bisect_utils.CheckRunGit(['reset', '--hard', 'HEAD'],
        cwd=self.depot_registry.GetDepotDir(culprit_depot))

  def _RevertCL(self, culprit_revision, culprit_depot):
    """Reverts the specified revision in the specified depot."""
    if self.opts.output_buildbot_annotations:
      bisect_utils.OutputAnnotationStepStart(
          'Reverting culprit CL: %s' % culprit_revision)
    _, return_code = bisect_utils.RunGit(
        ['revert', '--no-commit', culprit_revision],
        cwd=self.depot_registry.GetDepotDir(culprit_depot))
    if return_code:
      bisect_utils.OutputAnnotationStepWarning()
      bisect_utils.OutputAnnotationStepText('Failed to revert CL cleanly.')
    if self.opts.output_buildbot_annotations:
      bisect_utils.OutputAnnotationStepClosed()
    return not return_code

  def _RevertCulpritCLAndRetest(
      self, results, target_depot, command_to_run, metric):
    """Reverts the culprit CL against ToT and runs the performance test.

    Attempts to revert the culprit CL against ToT and runs the
    performance tests again with and without the CL.

    Args:
      results: BisectResults from the bisect.
      target_depot: The target depot we're bisecting.
      command_to_run: Specify the command to execute the performance test.
      metric: The performance metric to monitor.

    Returns:
      A tuple with the results of running the CL at ToT/reverted.
    """
    # Might want to retest ToT with a revert of the CL to confirm that
    # performance returns.
    if results.confidence < bisect_utils.HIGH_CONFIDENCE:
      return (None, None)

    # If there were multiple culprit CLs, we won't try to revert.
    if len(results.culprit_revisions) != 1:
      return (None, None)

    culprit_revision, _, culprit_depot = results.culprit_revisions[0]

    if not self._SyncRevision(target_depot, None, 'gclient'):
      return (None, None)

    head_revision = bisect_utils.CheckRunGit(['log', '--format=%H', '-1'])
    head_revision = head_revision.strip()

    if not self._RevertCL(culprit_revision, culprit_depot):
      return (None, None)

    # If the culprit CL happened to be in a depot that gets pulled in, we
    # can't revert the change and issue a try job to build, since that would
    # require modifying both the DEPS file and files in another depot.
    # Instead, we build locally.
    force_build = (culprit_depot != target_depot)
    if force_build:
      results.warnings.append(
          'Culprit CL is in another depot, attempting to revert and build'
          ' locally to retest. This may not match the performance of official'
          ' builds.')

    run_results_reverted = self._RunTestWithAnnotations(
        'Re-Testing ToT with reverted culprit',
        'Failed to run reverted CL.',
        head_revision, target_depot, command_to_run, metric, force_build)

    # Clear the reverted file(s).
    bisect_utils.RunGit(['reset', '--hard', 'HEAD'],
        cwd=self.depot_registry.GetDepotDir(culprit_depot))

    # Retesting with the reverted CL failed, so bail out of retesting against
    # ToT.
    if run_results_reverted[1]:
      return (None, None)

    run_results_tot = self._RunTestWithAnnotations(
        'Re-Testing ToT',
        'Failed to run ToT.',
        head_revision, target_depot, command_to_run, metric, force_build)

    return (run_results_tot, run_results_reverted)

  def _RunTestWithAnnotations(self, step_text, error_text, head_revision,
      target_depot, command_to_run, metric, force_build):
    """Runs the performance test and outputs start/stop annotations.

    Args:
      results: BisectResults from the bisect.
      target_depot: The target depot we're bisecting.
      command_to_run: Specify the command to execute the performance test.
      metric: The performance metric to monitor.
      force_build: Whether to force a build locally.

    Returns:
      Results of the test.
    """
    if self.opts.output_buildbot_annotations:
      bisect_utils.OutputAnnotationStepStart(step_text)

    # Build and run the test again with the reverted culprit CL against ToT.
    run_test_results = self.RunTest(
        head_revision, target_depot, command_to_run,
        metric, skippable=False, skip_sync=True, create_patch=True,
        force_build=force_build)

    if self.opts.output_buildbot_annotations:
      if run_test_results[1]:
        bisect_utils.OutputAnnotationStepWarning()
        bisect_utils.OutputAnnotationStepText(error_text)
      bisect_utils.OutputAnnotationStepClosed()

    return run_test_results

    if self.opts.target_platform == 'android-chrome':
    bad_revision = source_control.ResolveToRevision(
        bad_revision_in, target_depot, bisect_utils.DEPOT_DEPS_NAME, 100)
    good_revision = source_control.ResolveToRevision(
        good_revision_in, target_depot, bisect_utils.DEPOT_DEPS_NAME, -100)
      return BisectResults(
          error='Couldn\'t resolve [%s] to SHA1.' % bad_revision_in)
      return BisectResults(
          error='Couldn\'t resolve [%s] to SHA1.' % good_revision_in)
      return BisectResults(error='bad_revision < good_revision, did you swap '
                                 'these by mistake?')

      return BisectResults(error=cannot_bisect.get('error'))
    revision_list = self.GetRevisionList(target_depot, bad_revision,
                                         good_revision)
    if revision_list:
                                                             bad_revision,
                                                             command_to_run,
                                                             metric,
                                                             target_depot)
        error = ('An error occurred while building and running the \'bad\' '
                 'reference value. The bisect cannot continue without '
                 'a working \'bad\' revision to start from.\n\nError: %s' %
                 bad_results[0])
        return BisectResults(error=error)
        error = ('An error occurred while building and running the \'good\' '
                 'reference value. The bisect cannot continue without '
                 'a working \'good\' revision to start from.\n\nError: %s' %
                 good_results[0])
        return BisectResults(error=error)
      # Check the direction of improvement only if the improvement_direction
      # option is set to a specific direction (1 for higher is better or -1 for
      # lower is better).
      improvement_dir = self.opts.improvement_direction
      if improvement_dir:
        higher_is_better = improvement_dir > 0
        if higher_is_better:
          message = "Expecting higher values to be better for this metric, "
        else:
          message = "Expecting lower values to be better for this metric, "
        metric_increased = known_bad_value['mean'] > known_good_value['mean']
        if metric_increased:
          message += "and the metric appears to have increased. "
        else:
          message += "and the metric appears to have decreased. "
        if ((higher_is_better and metric_increased) or
            (not higher_is_better and not metric_increased)):
          error = (message + 'Then, the test results for the ends of the given '
                   '\'good\' - \'bad\' range of revisions represent an '
                   'improvement (and not a regression).')
          return BisectResults(error=error)
        logging.info(message + "Therefore we continue to bisect.")

      bisect_state = BisectState(target_depot, revision_list)
      revision_states = bisect_state.GetRevisionStates()

      min_revision = 0
      max_revision = len(revision_states) - 1

      bad_revision_state = revision_states[min_revision]
      bad_revision_state.external = bad_results[2]
      bad_revision_state.perf_time = bad_results[3]
      bad_revision_state.build_time = bad_results[4]
      bad_revision_state.passed = False
      bad_revision_state.value = known_bad_value

      good_revision_state = revision_states[max_revision]
      good_revision_state.external = good_results[2]
      good_revision_state.perf_time = good_results[3]
      good_revision_state.build_time = good_results[4]
      good_revision_state.passed = True
      good_revision_state.value = known_good_value

      # Check how likely it is that the good and bad results are different
      # beyond chance-induced variation.
      confidence_error = False
      if not self.opts.debug_ignore_regression_confidence:
        confidence_error = _CheckRegressionConfidenceError(good_revision,
                                                           bad_revision,
                                                           known_good_value,
                                                           known_bad_value)
        if confidence_error:
          self.warnings.append(confidence_error)
          bad_revision_state.passed = True # Marking the 'bad' revision as good.
          return BisectResults(bisect_state, self.depot_registry, self.opts,
                               self.warnings)
        if not revision_states:
          min_revision_state = revision_states[min_revision]
          max_revision_state = revision_states[max_revision]
          current_depot = min_revision_state.depot
          # TODO(sergiyb): Under which conditions can first two branches be hit?
          if min_revision_state.passed == '?':
          elif max_revision_state.passed == '?':
          elif current_depot in ['android-chrome', 'chromium', 'v8']:
            previous_revision = revision_states[min_revision].revision
                current_depot, min_revision_state, max_revision_state)
            earliest_revision = max_revision_state.external[external_depot]
            latest_revision = min_revision_state.external[external_depot]
                external_depot, earliest_revision, latest_revision,
              error = ('An error occurred attempting to retrieve revision '
                       'range: [%s..%s]' % (earliest_revision, latest_revision))
              return BisectResults(error=error)

            revision_states = bisect_state.CreateRevisionStatesAfter(
                external_depot, new_revision_list, current_depot,
                previous_revision)

            # Reset the bisection and perform it on the newly inserted states.
            max_revision = len(revision_states) - 1
            revision_list = [state.revision for state in revision_states]
        next_revision_state = revision_states[next_revision_index]
        next_revision = next_revision_state.revision
        next_depot = next_revision_state.depot
        self.depot_registry.ChangeToDepotDir(next_depot)
        message = 'Working on [%s:%s]' % (next_depot, next_revision)
        print message
          bisect_utils.OutputAnnotationStepStart(message)
        run_results = self.RunTest(next_revision, next_depot, command_to_run,
                                   metric, skippable=True)
            next_revision_state.external = run_results[2]
            next_revision_state.perf_time = run_results[3]
            next_revision_state.build_time = run_results[4]
          next_revision_state.passed = passed_regression
          next_revision_state.value = run_results[0]
            next_revision_state.passed = 'Skipped'
            next_revision_state.passed = 'Build Failed'
          revision_states.pop(next_revision_index)
          self.printer.PrintPartialResults(bisect_state)
      self._ConfidenceExtraTestRuns(min_revision_state, max_revision_state,
                                    command_to_run, metric)
      results = BisectResults(bisect_state, self.depot_registry, self.opts,
                              self.warnings)
      self._GatherResultsFromRevertedCulpritCL(
          results, target_depot, command_to_run, metric)
      return results
      # Weren't able to sync and retrieve the revision range.
      error = ('An error occurred attempting to retrieve revision range: '
               '[%s..%s]' % (good_revision, bad_revision))
      return BisectResults(error=error)

  def _ConfidenceExtraTestRuns(self, good_state, bad_state, command_to_run,
                               metric):
    if (bool(good_state.passed) != bool(bad_state.passed)
       and good_state.passed not in ('Skipped', 'Build Failed')
       and bad_state.passed not in ('Skipped', 'Build Failed')):
      for state in (good_state, bad_state):
        run_results = self.RunTest(
            state.revision,
            state.depot,
            command_to_run,
            metric,
            test_run_multiplier=BORDER_REVISIONS_EXTRA_RUNS)
        # Is extend the right thing to do here?
        if run_results[1] != BUILD_RESULT_FAIL:
          state.value['values'].extend(run_results[0]['values'])
        else:
          warning_text = 'Re-test of revision %s failed with error message: %s'
          warning_text %= (state.revision, run_results[0])
          if warning_text not in self.warnings:
            self.warnings.append(warning_text)
def RemoveBuildFiles(build_type):
  """Removes build files from previous runs."""
  out_dir = os.path.join('out', build_type)
  build_dir = os.path.join('build', build_type)
  logging.info('Removing build files in "%s" and "%s".',
               os.path.abspath(out_dir), os.path.abspath(build_dir))
  try:
    RemakeDirectoryTree(out_dir)
    RemakeDirectoryTree(build_dir)
  except Exception as e:
    raise RuntimeError('Got error in RemoveBuildFiles: %s' % e)
def RemakeDirectoryTree(path_to_dir):
  """Removes a directory tree and replaces it with an empty one.

  Returns True if successful, False otherwise.
  RemoveDirectoryTree(path_to_dir)
  MaybeMakeDirectory(path_to_dir)


def RemoveDirectoryTree(path_to_dir):
  """Removes a directory tree. Returns True if successful or False otherwise."""
  if os.path.isfile(path_to_dir):
    logging.info('REMOVING FILE %s' % path_to_dir)
    os.remove(path_to_dir)
      raise
# This is copied from build/scripts/common/chromium_utils.py.
def MaybeMakeDirectory(*path):
  """Creates an entire path, if it doesn't already exist."""
  file_path = os.path.join(*path)
  try:
    os.makedirs(file_path)
  except OSError as e:
    if e.errno != errno.EEXIST:
      raise
    self.goma_threads = 64
    self.debug_ignore_regression_confidence = None
    self.debug_fake_first_test_mean = 0
    self.builder_type = 'perf'
    self.bisect_mode = bisect_utils.BISECT_MODE_MEAN
    self.improvement_direction = 0
    self.bug_id = ''

  @staticmethod
  def _AddBisectOptionsGroup(parser):
    group = parser.add_argument_group('Bisect options')
    group.add_argument('-c', '--command', required=True,
                       help='A command to execute your performance test at '
                            'each point in the bisection.')
    group.add_argument('-b', '--bad_revision', required=True,
                       help='A bad revision to start bisection. Must be later '
                            'than good revision. May be either a git or svn '
                            'revision.')
    group.add_argument('-g', '--good_revision', required=True,
                       help='A revision to start bisection where performance '
                            'test is known to pass. Must be earlier than the '
                            'bad revision. May be either a git or a svn '
                            'revision.')
    group.add_argument('-m', '--metric',
                       help='The desired metric to bisect on. For example '
                            '"vm_rss_final_b/vm_rss_f_b"')
    group.add_argument('-d', '--improvement_direction', type=int, default=0,
                       help='An integer number representing the direction of '
                            'improvement. 1 for higher is better, -1 for lower '
                            'is better, 0 for ignore (default).')
    group.add_argument('-r', '--repeat_test_count', type=int, default=20,
                       choices=range(1, 101),
                       help='The number of times to repeat the performance '
                            'test. Values will be clamped to range [1, 100]. '
                            'Default value is 20.')
    group.add_argument('--max_time_minutes', type=int, default=20,
                       choices=range(1, 61),
                       help='The maximum time (in minutes) to take running the '
                            'performance tests. The script will run the '
                            'performance tests according to '
                            '--repeat_test_count, so long as it doesn\'t exceed'
                            ' --max_time_minutes. Values will be clamped to '
                            'range [1, 60]. Default value is 20.')
    group.add_argument('-t', '--truncate_percent', type=int, default=25,
                       help='The highest/lowest percent are discarded to form '
                            'a truncated mean. Values will be clamped to range '
                            '[0, 25]. Default value is 25 percent.')
    group.add_argument('--bisect_mode', default=bisect_utils.BISECT_MODE_MEAN,
                       choices=[bisect_utils.BISECT_MODE_MEAN,
                                bisect_utils.BISECT_MODE_STD_DEV,
                                bisect_utils.BISECT_MODE_RETURN_CODE],
                       help='The bisect mode. Choices are to bisect on the '
                            'difference in mean, std_dev, or return_code.')
    group.add_argument('--bug_id', default='',
                       help='The id for the bug associated with this bisect. ' +
                            'If this number is given, bisect will attempt to ' +
                            'verify that the bug is not closed before '
                            'starting.')

  @staticmethod
  def _AddBuildOptionsGroup(parser):
    group = parser.add_argument_group('Build options')
    group.add_argument('-w', '--working_directory',
                       help='Path to the working directory where the script '
                       'will do an initial checkout of the chromium depot. The '
                       'files will be placed in a subdirectory "bisect" under '
                       'working_directory and that will be used to perform the '
                       'bisection. This parameter is optional, if it is not '
                       'supplied, the script will work from the current depot.')
    group.add_argument('--build_preference',
                       choices=['msvs', 'ninja', 'make'],
                       help='The preferred build system to use. On linux/mac '
                            'the options are make/ninja. On Windows, the '
                            'options are msvs/ninja.')
    group.add_argument('--target_platform', default='chromium',
                       choices=['chromium', 'android', 'android-chrome'],
                       help='The target platform. Choices are "chromium" '
                            '(current platform), or "android". If you specify '
                            'something other than "chromium", you must be '
                            'properly set up to build that platform.')
    group.add_argument('--no_custom_deps', dest='no_custom_deps',
                       action='store_true', default=False,
                       help='Run the script with custom_deps or not.')
    group.add_argument('--extra_src',
                       help='Path to a script which can be used to modify the '
                            'bisect script\'s behavior.')
    group.add_argument('--use_goma', action='store_true',
                       help='Add a bunch of extra threads for goma, and enable '
                            'goma')
    group.add_argument('--goma_dir',
                       help='Path to goma tools (or system default if not '
                            'specified).')
    group.add_argument('--goma_threads', type=int, default='64',
                       help='Number of threads for goma, only if using goma.')
    group.add_argument('--output_buildbot_annotations', action='store_true',
                       help='Add extra annotation output for buildbot.')
    group.add_argument('--target_arch', default='ia32',
                       dest='target_arch', choices=['ia32', 'x64', 'arm'],
                       help='The target build architecture. Choices are "ia32" '
                            '(default), "x64" or "arm".')
    group.add_argument('--target_build_type', default='Release',
                       choices=['Release', 'Debug'],
                       help='The target build type. Choices are "Release" '
                            '(default), or "Debug".')
    group.add_argument('--builder_type', default=fetch_build.PERF_BUILDER,
                       choices=[fetch_build.PERF_BUILDER,
                                fetch_build.FULL_BUILDER, ''],
                       help='Type of builder to get build from. This '
                            'determines both the bot that builds and the '
                            'place where archived builds are downloaded from. '
                            'For local builds, an empty string can be passed.')
  def _AddDebugOptionsGroup(parser):
    group = parser.add_argument_group('Debug options')
    group.add_argument('--debug_ignore_build', action='store_true',
                       help='DEBUG: Don\'t perform builds.')
    group.add_argument('--debug_ignore_sync', action='store_true',
                       help='DEBUG: Don\'t perform syncs.')
    group.add_argument('--debug_ignore_perf_test', action='store_true',
                       help='DEBUG: Don\'t perform performance tests.')
    group.add_argument('--debug_ignore_regression_confidence',
                       action='store_true',
                       help='DEBUG: Don\'t score the confidence of the initial '
                            'good and bad revisions\' test results.')
    group.add_argument('--debug_fake_first_test_mean', type=int, default='0',
                       help='DEBUG: When faking performance tests, return this '
                            'value as the mean of the first performance test, '
                            'and return a mean of 0.0 for further tests.')
    return group

  @classmethod
  def _CreateCommandLineParser(cls):
      An instance of argparse.ArgumentParser.
    usage = ('%(prog)s [options] [-- chromium-options]\n'
    parser = argparse.ArgumentParser(usage=usage)
    cls._AddBisectOptionsGroup(parser)
    cls._AddBuildOptionsGroup(parser)
    cls._AddDebugOptionsGroup(parser)
    opts = parser.parse_args()
      if (not opts.metric and
          opts.bisect_mode != bisect_utils.BISECT_MODE_RETURN_CODE):
      if opts.bisect_mode != bisect_utils.BISECT_MODE_RETURN_CODE:
      opts.truncate_percent = min(max(opts.truncate_percent, 0), 25) / 100.0
    if opts.metric and opts.bisect_mode != bisect_utils.BISECT_MODE_RETURN_CODE:
def _ConfigureLogging():
  """Trivial logging config.
  Configures logging to output any messages at or above INFO to standard out,
  without any additional formatting.
  """
  logging_format = '%(message)s'
  logging.basicConfig(
      stream=logging.sys.stdout, level=logging.INFO, format=logging_format)


def main():
  _ConfigureLogging()
    if opts.bug_id:
      if opts.output_buildbot_annotations:
        bisect_utils.OutputAnnotationStepStart('Checking Issue Tracker')
      issue_closed = query_crbug.CheckIssueClosed(opts.bug_id)
      if issue_closed:
        print 'Aborting bisect because bug is closed'
      else:
        print 'Could not confirm bug is closed, proceeding.'
      if opts.output_buildbot_annotations:
        bisect_utils.OutputAnnotationStepClosed()
      if issue_closed:
        results = BisectResults(abort_reason='the bug is closed.')
        bisect_test = BisectPerformanceMetrics(opts, os.getcwd())
        bisect_test.printer.FormatAndPrintResults(results)
        return 0


      bisect_utils.AddAdditionalDepotInfo(extra_src.GetAdditionalDepotInfo())
      RemoveBuildFiles(opts.target_build_type)
    if not source_control.IsInGitRepository():
    bisect_test = BisectPerformanceMetrics(opts, os.getcwd())
      results = bisect_test.Run(opts.command, opts.bad_revision,
                                opts.good_revision, opts.metric)
      if results.error:
        raise RuntimeError(results.error)
      bisect_test.printer.FormatAndPrintResults(results)
  except RuntimeError as e:
    print 'Runtime Error: %s' % e