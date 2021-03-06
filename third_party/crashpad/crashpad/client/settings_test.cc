// Copyright 2015 The Crashpad Authors. All rights reserved.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

#include "client/settings.h"

#include "gtest/gtest.h"
#include "util/file/file_io.h"
#include "util/test/scoped_temp_dir.h"

namespace crashpad {
namespace test {
namespace {

class SettingsTest : public testing::Test {
 public:
  SettingsTest() : settings_(settings_path()) {}

  base::FilePath settings_path() {
    return temp_dir_.path().Append("settings");
  }

  Settings* settings() { return &settings_; }

  void InitializeBadFile() {
    ScopedFileHandle handle(
        LoggingOpenFileForWrite(settings_path(),
                                FileWriteMode::kTruncateOrCreate,
                                FilePermissions::kWorldReadable));
    ASSERT_TRUE(handle.is_valid());

    const char kBuf[] = "test bad file";
    ASSERT_TRUE(LoggingWriteFile(handle.get(), kBuf, sizeof(kBuf)));
    handle.reset();
  }

 protected:
  // testing::Test:
  void SetUp() override {
    ASSERT_TRUE(settings()->Initialize());
  }

 private:
  ScopedTempDir temp_dir_;
  Settings settings_;

  DISALLOW_COPY_AND_ASSIGN(SettingsTest);
};

TEST_F(SettingsTest, ClientID) {
  UUID client_id;
  EXPECT_TRUE(settings()->GetClientID(&client_id));
  EXPECT_NE(UUID(), client_id);

  Settings local_settings(settings_path());
  EXPECT_TRUE(local_settings.Initialize());
  UUID actual;
  EXPECT_TRUE(local_settings.GetClientID(&actual));
  EXPECT_EQ(client_id, actual);
}

TEST_F(SettingsTest, UploadsEnabled) {
  bool enabled = true;
  // Default value is false.
  EXPECT_TRUE(settings()->GetUploadsEnabled(&enabled));
  EXPECT_FALSE(enabled);

  EXPECT_TRUE(settings()->SetUploadsEnabled(true));
  EXPECT_TRUE(settings()->GetUploadsEnabled(&enabled));
  EXPECT_TRUE(enabled);

  Settings local_settings(settings_path());
  EXPECT_TRUE(local_settings.Initialize());
  enabled = false;
  EXPECT_TRUE(local_settings.GetUploadsEnabled(&enabled));
  EXPECT_TRUE(enabled);

  EXPECT_TRUE(settings()->SetUploadsEnabled(false));
  EXPECT_TRUE(settings()->GetUploadsEnabled(&enabled));
  EXPECT_FALSE(enabled);

  enabled = true;
  EXPECT_TRUE(local_settings.GetUploadsEnabled(&enabled));
  EXPECT_FALSE(enabled);
}

TEST_F(SettingsTest, LastUploadAttemptTime) {
  time_t actual = -1;
  EXPECT_TRUE(settings()->GetLastUploadAttemptTime(&actual));
  // Default value is 0.
  EXPECT_EQ(0, actual);

  const time_t expected = time(nullptr);
  EXPECT_TRUE(settings()->SetLastUploadAttemptTime(expected));
  EXPECT_TRUE(settings()->GetLastUploadAttemptTime(&actual));
  EXPECT_EQ(expected, actual);

  Settings local_settings(settings_path());
  EXPECT_TRUE(local_settings.Initialize());
  actual = -1;
  EXPECT_TRUE(local_settings.GetLastUploadAttemptTime(&actual));
  EXPECT_EQ(expected, actual);
}

// The following tests write a corrupt settings file and test the recovery
// operation.

TEST_F(SettingsTest, BadFileOnInitialize) {
  InitializeBadFile();

  Settings settings(settings_path());
  EXPECT_TRUE(settings.Initialize());
}

TEST_F(SettingsTest, BadFileOnGet) {
  InitializeBadFile();

  UUID client_id;
  EXPECT_TRUE(settings()->GetClientID(&client_id));
  EXPECT_NE(UUID(), client_id);

  Settings local_settings(settings_path());
  EXPECT_TRUE(local_settings.Initialize());
  UUID actual;
  EXPECT_TRUE(local_settings.GetClientID(&actual));
  EXPECT_EQ(client_id, actual);
}

TEST_F(SettingsTest, BadFileOnSet) {
  InitializeBadFile();

  EXPECT_TRUE(settings()->SetUploadsEnabled(true));
  bool enabled = false;
  EXPECT_TRUE(settings()->GetUploadsEnabled(&enabled));
  EXPECT_TRUE(enabled);
}

TEST_F(SettingsTest, UnlinkFile) {
  UUID client_id;
  EXPECT_TRUE(settings()->GetClientID(&client_id));
  EXPECT_TRUE(settings()->SetUploadsEnabled(true));
  EXPECT_TRUE(settings()->SetLastUploadAttemptTime(time(nullptr)));

  EXPECT_EQ(0, unlink(settings_path().value().c_str()));

  Settings local_settings(settings_path());
  EXPECT_TRUE(local_settings.Initialize());
  UUID new_client_id;
  EXPECT_TRUE(local_settings.GetClientID(&new_client_id));
  EXPECT_NE(client_id, new_client_id);

  // Check that all values are reset.
  bool enabled = true;
  EXPECT_TRUE(local_settings.GetUploadsEnabled(&enabled));
  EXPECT_FALSE(enabled);

  time_t time = -1;
  EXPECT_TRUE(local_settings.GetLastUploadAttemptTime(&time));
  EXPECT_EQ(0, time);
}

}  // namespace
}  // namespace test
}  // namespace crashpad
