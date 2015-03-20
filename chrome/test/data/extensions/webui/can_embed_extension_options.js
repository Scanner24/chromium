// Copyright 2014 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

// out/Debug/browser_tests
//     --gtest_filter=ExtensionWebUITest.CanEmbedExtensionOptions
if (!chrome || !chrome.test || !chrome.test.sendMessage) {
  console.error('chrome.test.sendMessage is unavailable on ' +
                document.location.href);
  domAutomationController.send(false);
  return;
}

chrome.test.sendMessage('ready', function(reply) {
  var extensionoptions = document.createElement('extensionoptions');
  extensionoptions.addEventListener('load', function() {
    chrome.test.sendMessage('guest loaded');
  });
  extensionoptions.setAttribute('extension', reply);
  document.body.appendChild(extensionoptions);
});

domAutomationController.send(true);