// Copyright 2014 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#include "ui/chromeos/user_activity_power_manager_notifier.h"

#include "chromeos/dbus/dbus_thread_manager.h"
#include "chromeos/dbus/power_manager_client.h"
#include "ui/events/event.h"
#include "ui/events/event_constants.h"
#include "ui/events/keycodes/keyboard_codes_posix.h"
#include "ui/wm/core/user_activity_detector.h"

namespace ui {
namespace {

// Minimum number of seconds between notifications.
const int kNotifyIntervalSec = 5;

// Returns a UserActivityType describing |event|.
power_manager::UserActivityType GetUserActivityTypeForEvent(
    const Event* event) {
  if (!event || event->type() != ET_KEY_PRESSED)
    return power_manager::USER_ACTIVITY_OTHER;

  switch (static_cast<const KeyEvent*>(event)->key_code()) {
    case VKEY_BRIGHTNESS_DOWN:
      return power_manager::USER_ACTIVITY_BRIGHTNESS_DOWN_KEY_PRESS;
    case VKEY_BRIGHTNESS_UP:
      return power_manager::USER_ACTIVITY_BRIGHTNESS_UP_KEY_PRESS;
    case VKEY_VOLUME_DOWN:
      return power_manager::USER_ACTIVITY_VOLUME_DOWN_KEY_PRESS;
    case VKEY_VOLUME_MUTE:
      return power_manager::USER_ACTIVITY_VOLUME_MUTE_KEY_PRESS;
    case VKEY_VOLUME_UP:
      return power_manager::USER_ACTIVITY_VOLUME_UP_KEY_PRESS;
    default:
      return power_manager::USER_ACTIVITY_OTHER;
  }
}

}  // namespace

UserActivityPowerManagerNotifier::UserActivityPowerManagerNotifier(
    ::wm::UserActivityDetector* detector)
    : detector_(detector) {
  detector_->AddObserver(this);
}

UserActivityPowerManagerNotifier::~UserActivityPowerManagerNotifier() {
  detector_->RemoveObserver(this);
}

void UserActivityPowerManagerNotifier::OnUserActivity(const Event* event) {
  base::TimeTicks now = base::TimeTicks::Now();
  // InSeconds() truncates rather than rounding, so it's fine for this
  // comparison.
  if (last_notify_time_.is_null() ||
      (now - last_notify_time_).InSeconds() >= kNotifyIntervalSec) {
    chromeos::DBusThreadManager::Get()->GetPowerManagerClient()->
        NotifyUserActivity(GetUserActivityTypeForEvent(event));
    last_notify_time_ = now;
  }
}

}  // namespace ui
