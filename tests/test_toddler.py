"""
Toddler TV — The Toddler Test Suite
════════════════════════════════════
A rigorous simulation of a 2-year-old with unrestricted access to the remote.

Test methodology: a toddler does not read the instructions.
A toddler does not wait for things to load.
A toddler does not understand the concept of "nap time".
A toddler will press the power button exactly as many times as it takes to
make something happen, and then thirty more times.

These tests verify that the application survives contact with a small human.
"""

import os
import random
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

# ── Path & stubs ───────────────────────────────────────────────────────────────
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

# setdefault returns the existing entry when tests share a process with test_e2e.py,
# so we always get the same mock object that toddlertv.py actually imported.
_mock_vlc = sys.modules.setdefault("vlc", MagicMock())
_mock_vlc.State.Playing = 3
_mock_vlc.State.Error   = 5
_mock_vlc.State.Ended   = 6
_mock_vlc.State.Stopped = 0
sys.modules.setdefault("yt_dlp", MagicMock())

# ── Project imports ────────────────────────────────────────────────────────────
import schedule as _schedule_module                              # noqa: E402
import images as _images_module                                  # noqa: E402
import toddlertv as _tt                                          # noqa: E402

AppState = _tt.AppState
N        = _tt.N   # number of channels

_VALID_STATES = set(AppState)
_VALID_CHANNELS = set(range(N))


def _reset_schedule_cache():
    _schedule_module._cached_periods = []
    _schedule_module._cached_mtime   = 1.0


# ══════════════════════════════════════════════════════════════════════════════
#  Toddler test harness
#  (same app setup as TestAppStateMachine — a hidden tk root + mocked VLC)
# ══════════════════════════════════════════════════════════════════════════════

class ToddlerTestCase(unittest.TestCase):
    """Base class: boots the app with mocked VLC and a hidden window."""

    @classmethod
    def setUpClass(cls):
        import tkinter as tk
        cls._tk = tk

    def setUp(self):
        self._player = MagicMock()
        self._player.get_state.return_value       = _mock_vlc.State.Playing
        self._player.get_time.return_value        = 0
        self._player.audio_get_mute.return_value  = False
        _mock_vlc.Instance.return_value.media_player_new.return_value = self._player

        _images_module._avatar_cache.clear()
        _reset_schedule_cache()

        self._patchers = [
            patch.object(_tt.ToddlerTV, "_build_ui"),
            patch.object(_tt.ToddlerTV, "_attach_vlc_to_window"),
            patch.object(_tt.ToddlerTV, "_show_robots"),
            patch.object(_tt.ToddlerTV, "_hide_robots"),
            patch.object(_tt.ToddlerTV, "_start_mini_robots"),
            patch.object(_tt.ToddlerTV, "_stop_mini_robots"),
            patch.object(_tt.ToddlerTV, "_update_buttons"),
            patch.object(_tt.ToddlerTV, "_update_clock"),
            patch.object(_tt.ToddlerTV, "_resolve_channel"),
            patch("toddlertv.start_web_remote"),
        ]
        for p in self._patchers:
            p.start()

        self._root = self._tk.Tk()
        self._root.withdraw()
        self.app = _tt.ToddlerTV(self._root)

        self.app.video_frame        = MagicMock()
        self.app.robot_canvas       = MagicMock()
        self.app.channel_buttons    = [MagicMock() for _ in range(N)]
        self.app.channel_info_label = MagicMock()
        self.app.power_btn          = MagicMock()
        self.app.bottom_panel       = MagicMock()
        self.app.clock_label        = MagicMock()
        self.app.static_canvas      = MagicMock()

        # Give every channel a resolved, ready state so button presses aren't
        # blocked by "still loading" guards.
        for state in self.app.states:
            state.ready  = True
            state.videos = [{
                "yt_url":       "https://www.youtube.com/watch?v=peppa",
                "stream_url":   "C:/videos/peppa.mp4",
                "duration":     1200.0,
                "_resolved_at": time.time(),
            }]
            state._rebuild_offsets()

        self.app.state = AppState.PLAYING

    def tearDown(self):
        time.sleep(0.15)
        for p in self._patchers:
            p.stop()
        try:
            self._root.destroy()
        except Exception:
            pass
        _reset_schedule_cache()

    # ── Assertion helpers ─────────────────────────────────────────────────────

    def assertAppSane(self):
        """The app must be in a valid state after whatever just happened."""
        self.assertIn(self.app.state, _VALID_STATES,
                      f"State machine exploded: {self.app.state!r}")
        self.assertIn(self.app.current_channel, _VALID_CHANNELS,
                      f"current_channel went off-map: {self.app.current_channel}")
        # Clock should not have gone negative somehow
        self.assertGreaterEqual(self.app.clock.elapsed(), 0.0,
                                "Clock travelled backwards in time")

    def playing(self):
        """Force the app into PLAYING so buttons are live."""
        self.app.state = AppState.PLAYING


# ══════════════════════════════════════════════════════════════════════════════
#  ACT I: THE DISCOVERY PHASE
#  (toddler has just found the remote)
# ══════════════════════════════════════════════════════════════════════════════

class TestToddlerDiscovery(ToddlerTestCase):

    def test_toddler_presses_button_once_politely(self):
        """
        An unrealistically well-behaved interaction included for completeness
        and to establish a baseline before everything goes sideways.
        """
        self.app.switch_channel(1)
        self.assertAppSane()

    def test_toddler_presses_same_button_until_something_happens(self):
        """
        Spoiler: nothing new happens after the first press.
        The toddler will not discover this for another 47 attempts.
        """
        for _ in range(47):
            self.app.switch_channel(0)

        self.assertAppSane()
        # Repeatedly pressing the active channel is a no-op
        self.assertEqual(self.app.current_channel, 0)

    def test_toddler_discovers_there_are_multiple_buttons(self):
        """A methodical sweep of every available channel button. Once."""
        for ch in range(N):
            self.playing()
            self.app.switch_channel(ch)

        self.assertAppSane()
        self.assertEqual(self.app.current_channel, N - 1)

    def test_toddler_does_a_full_lap_around_the_channels_repeatedly(self):
        """
        Going round and round, faster and faster.
        Scientifically proven to be funnier each lap.
        """
        for _ in range(10):
            for ch in range(N):
                self.playing()
                self.app.switch_channel(ch)

        self.assertAppSane()


# ══════════════════════════════════════════════════════════════════════════════
#  ACT II: THE POWER BUTTON ERA
#  (toddler has located the power button and is extremely pleased about it)
# ══════════════════════════════════════════════════════════════════════════════

class TestToddlerPowerButton(ToddlerTestCase):

    def test_toddler_turns_tv_off(self):
        """A bold choice. Immediately regretted."""
        self.app._power_off()
        self.assertEqual(self.app.state, AppState.POWER_OFF)
        self.assertAppSane()

    def test_toddler_turns_tv_off_then_immediately_demands_it_back(self):
        """The full emotional arc, compressed into two function calls."""
        self.app._power_off()
        with patch("toddlertv.is_tv_off", return_value=False):
            self.app._power_on()
        self.assertAppSane()
        self.assertNotEqual(self.app.state, AppState.POWER_OFF)

    def test_toddler_has_learned_what_the_power_button_does_and_is_very_excited(self):
        """
        Twenty rapid toggles.
        The TV survives. The parent's sanity does not.
        """
        for i in range(20):
            if i % 2 == 0:
                self.app._power_off()
            else:
                with patch("toddlertv.is_tv_off", return_value=False), \
                     patch.object(self.app, "_start_channel"):
                    self.app._power_on()

        self.assertAppSane()
        # After an even number of toggles the TV ends on POWER_OFF
        self.assertEqual(self.app.state, AppState.POWER_OFF)

    def test_toddler_smashes_power_button_without_any_strategy_whatsoever(self):
        """
        Forty presses. No plan. No remorse.
        We just check nothing exploded.
        """
        for _ in range(40):
            with patch("toddlertv.is_tv_off", return_value=False), \
                 patch("toddlertv.current_scene_name", return_value="sleeping"), \
                 patch.object(self.app, "_start_channel"):
                self.app._toggle_power()

        self.assertAppSane()

    def test_toddler_turns_tv_off_then_asks_why_its_off(self):
        """
        Classic. Power off, then immediate BOOTING check.
        The answer is always "because you turned it off, sweetheart."
        """
        self.app._power_off()
        self.assertEqual(self.app.state, AppState.POWER_OFF)
        # VLC must have been told to stop
        self._player.stop.assert_called()


# ══════════════════════════════════════════════════════════════════════════════
#  ACT III: NAPTIME NEGOTIATIONS
#  (toddler disputes the concept of scheduled off-periods)
# ══════════════════════════════════════════════════════════════════════════════

class TestToddlerNaptimeNegotiations(ToddlerTestCase):

    def _enter_naptime(self):
        """Put the TV into scheduled off mode (it is, objectively, nap time)."""
        with patch("toddlertv.current_scene_name", return_value="sleeping"):
            self.app._enter_schedule_off()

    def test_toddler_is_informed_it_is_nap_time(self):
        """The TV agrees. The toddler does not."""
        self._enter_naptime()
        self.assertEqual(self.app.state, AppState.SCHEDULE_OFF)

    def test_toddler_presses_every_channel_button_during_nap_time(self):
        """
        All channel switches are vetoed by the schedule.
        The channels do not care about the toddler's feelings.
        """
        self._enter_naptime()
        for _ in range(30):
            for ch in range(N):
                self.app.switch_channel(ch)

        # Must still be in schedule off — no channel switch went through
        self.assertEqual(self.app.state, AppState.SCHEDULE_OFF)
        self.assertAppSane()

    def test_toddler_attempts_to_negotiate_via_power_button(self):
        """
        Power-off during SCHEDULE_OFF → still POWER_OFF.
        Power-on during SCHEDULE_OFF → still SCHEDULE_OFF (schedule wins).
        """
        self._enter_naptime()
        self.app._power_off()
        # TV is off (toddler wins round 1)
        self.assertEqual(self.app.state, AppState.POWER_OFF)

        # Toddler turns it back on: schedule says no
        with patch("toddlertv.is_tv_off", return_value=True), \
             patch("toddlertv.current_scene_name", return_value="sleeping"), \
             patch.object(self.app, "_enter_schedule_off") as mock_off:
            self.app._power_on()

        # Schedule still wins
        mock_off.assert_called_once()
        self.assertAppSane()

    def test_toddler_repeatedly_demands_peppa_pig_at_3am(self):
        """
        One hundred and eleven channel-switch attempts between midnight and 6am.
        Peppa Pig is not available. The robot is sleeping.
        The answer is no.
        """
        self._enter_naptime()
        self.app.root.after = lambda *a, **kw: None  # don't schedule callbacks

        for _ in range(111):
            self.app.switch_channel(random.randint(0, N - 1))

        self.assertEqual(self.app.state, AppState.SCHEDULE_OFF)
        self._player.play.assert_not_called()  # VLC stays silent

    def test_naptime_ends_and_toddler_is_immediately_back(self):
        """
        The moment the schedule lifts, the TV resumes.
        The toddler was waiting. The toddler is always waiting.
        """
        self._enter_naptime()
        with patch.object(self.app, "_start_channel") as mock_start:
            self.app._leave_schedule_off()

        mock_start.assert_called_once_with(self.app.current_channel)
        self.assertAppSane()


# ══════════════════════════════════════════════════════════════════════════════
#  ACT IV: THE LOADING SCREEN
#  (toddler does not understand that LOADING means WAIT)
# ══════════════════════════════════════════════════════════════════════════════

class TestToddlerLoadingScreen(ToddlerTestCase):

    def test_toddler_switches_channel_while_previous_channel_is_loading(self):
        """
        Switching channels while in LOADING is ignored by design.
        The toddler does not know this. The toddler will try anyway.
        """
        self.app.state = AppState.LOADING
        self.app.current_channel = 0

        for ch in range(N):
            self.app.switch_channel(ch)

        # Still loading — all presses blocked
        self.assertEqual(self.app.state, AppState.LOADING)
        self.assertAppSane()

    def test_toddler_panic_switches_seventeen_channels_before_video_starts(self):
        """
        A frenetic sequence: PLAYING → switch → LOADING → switch switch switch
        → TV picks one and gets on with it.
        """
        # First switch goes through (PLAYING → LOADING)
        self.playing()
        with patch.object(self.app, "_play_video_for_channel"):
            self.app.switch_channel(1)
        self.assertEqual(self.app.state, AppState.LOADING)

        # Seventeen more presses while loading — all absorbed
        for ch in range(17):
            self.app.switch_channel(ch % N)

        self.assertEqual(self.app.state, AppState.LOADING)
        self.assertAppSane()

    def test_toddler_gives_up_and_loading_finishes_eventually(self):
        """
        Toddler exhausts their button-mashing budget.
        Loading completes. Silence falls.
        """
        self.app.state = AppState.LOADING
        for _ in range(50):
            self.app.switch_channel(random.randint(0, N - 1))

        # Simulate VLC finally starting
        self.app._finish_loading()

        self.assertEqual(self.app.state, AppState.PLAYING)
        self.assertAppSane()


# ══════════════════════════════════════════════════════════════════════════════
#  ACT V: THE CHAOS PHASE
#  (toddler has been given unsupervised access and is going full gremlin)
# ══════════════════════════════════════════════════════════════════════════════

class TestToddlerChaos(ToddlerTestCase):

    def _random_action(self, rng: random.Random):
        """
        A single random thing a toddler might do.
        All equally valid from the toddler's perspective.
        """
        action = rng.randint(0, 5)
        if action == 0:
            # Smash a channel button
            self.app.switch_channel(rng.randint(0, N - 1))
        elif action == 1:
            # Hit power
            with patch("toddlertv.is_tv_off", return_value=False), \
                 patch("toddlertv.current_scene_name", return_value="sleeping"), \
                 patch.object(self.app, "_start_channel"):
                self.app._toggle_power()
        elif action == 2:
            # Attempt to finish loading (sometimes VLC does cooperate)
            if self.app.state == AppState.LOADING:
                self.app._finish_loading()
        elif action == 3:
            # Re-enter playing (parent walks back in the room)
            self.playing()
        elif action == 4:
            # Smash the same channel 5 times in a row
            ch = rng.randint(0, N - 1)
            for _ in range(5):
                self.app.switch_channel(ch)
        else:
            # Briefly turn off and immediately turn back on
            self.app._power_off()
            with patch("toddlertv.is_tv_off", return_value=False), \
                 patch.object(self.app, "_start_channel"):
                self.app._power_on()

    def test_toddler_sits_on_the_remote(self):
        """
        Two hundred random actions. Fixed seed for reproducibility.
        The app must emerge from this with a valid state.

        (In the real world, sitting on the remote also somehow changes the
        input language on the TV, but that's out of scope for this test.)
        """
        rng = random.Random(42)  # Reproducible chaos
        for _ in range(200):
            self._random_action(rng)

        self.assertAppSane()

    def test_toddler_with_different_sitting_position(self):
        """Same test, different seed. The chaos is real, not scripted."""
        rng = random.Random(99)
        for _ in range(200):
            self._random_action(rng)

        self.assertAppSane()

    def test_vlc_is_not_playing_during_power_off(self):
        """
        After the toddler's rampage, if the TV is off, VLC must be stopped.
        This is non-negotiable.
        """
        rng = random.Random(7)
        for _ in range(100):
            self._random_action(rng)

        # Force power off as the final act
        self.app._power_off()

        self.assertEqual(self.app.state, AppState.POWER_OFF)
        self._player.stop.assert_called()

    def test_current_channel_never_escapes_valid_range(self):
        """
        No matter what, current_channel must always point to a real channel.
        There is no Channel −1. There is no Channel 9000.
        """
        rng = random.Random(13)
        for _ in range(300):
            self._random_action(rng)
            self.assertIn(self.app.current_channel, _VALID_CHANNELS,
                          f"Toddler broke current_channel={self.app.current_channel}")

    def test_clock_elapsed_is_always_non_negative(self):
        """
        Time moves forward. Always. Even for toddlers.
        """
        rng = random.Random(21)
        for _ in range(200):
            self._random_action(rng)
            self.assertGreaterEqual(self.app.clock.elapsed(), 0.0,
                                    "The clock went negative. Physics broke.")

    def test_app_state_is_always_a_known_state(self):
        """
        The state machine must never end up in an undocumented state.
        "Angry", "Confused", and "On Fire" are not valid AppStates.
        """
        rng = random.Random(55)
        for _ in range(300):
            self._random_action(rng)
            self.assertIn(self.app.state, _VALID_STATES,
                          f"Unknown state: {self.app.state!r}")


# ══════════════════════════════════════════════════════════════════════════════
#  ACT VI: THE EDGE CASES
#  (things no reasonable person would do, and therefore a toddler will do)
# ══════════════════════════════════════════════════════════════════════════════

class TestToddlerEdgeCases(ToddlerTestCase):

    def test_toddler_switches_channel_the_instant_the_tv_boots(self):
        """
        Before anything has loaded — state is BOOTING.
        switch_channel during BOOTING is actually permitted by the guard
        (`if self.state not in (PLAYING, BOOTING): return`), so the channel
        switch goes through and must not crash.
        """
        self.app.state = AppState.BOOTING
        self.app.current_channel = 0

        with patch.object(self.app, "_play_video_for_channel"):
            self.app.switch_channel(1)

        self.assertAppSane()

    def test_toddler_finishes_loading_twice_in_a_row(self):
        """
        A duplicate _finish_loading call (e.g., both the VLC callback and
        the safety-unmute fire at the same time) must be a no-op on the second
        call — not double-unmute or double clock-resume.
        """
        self.app.state = AppState.LOADING
        self.app._finish_loading()   # legitimate finish
        self.app._finish_loading()   # duplicate; must be silently ignored

        self.assertEqual(self.app.state, AppState.PLAYING)
        # audio_set_mute(False) called exactly once (the second call is short-circuited)
        mute_calls = [c for c in self._player.audio_set_mute.call_args_list
                      if c == ((False,), {})]
        self.assertEqual(len(mute_calls), 1)

    def test_toddler_turns_tv_off_and_on_before_first_video_loads(self):
        """
        Power cycle during BOOTING.
        The app must not start playing before channels are ready.
        """
        self.app.state = AppState.BOOTING
        for s in self.app.states:
            s.ready  = False
            s.videos = []

        self.app._power_off()
        self.assertEqual(self.app.state, AppState.POWER_OFF)

        with patch("toddlertv.is_tv_off", return_value=False), \
             patch.object(self.app, "_start_channel") as mock_start:
            self.app._power_on()

        # _start_channel is called — it will then discover the channel isn't
        # ready and show the loading robots. This is correct behaviour.
        mock_start.assert_called_once()
        self.assertAppSane()

    def test_toddler_exhausts_all_channels_then_tries_non_existent_one(self):
        """
        Channels 0..N-1 exist. The toddler has a hypothesis about channel N.
        The hypothesis is wrong.

        switch_channel(N) hits CHANNELS[N] which raises IndexError —
        this test documents the current behaviour so any future fix is caught.
        """
        self.playing()
        with self.assertRaises((IndexError, Exception)):
            self.app.switch_channel(N)  # one past the end


if __name__ == "__main__":
    unittest.main(verbosity=2)
