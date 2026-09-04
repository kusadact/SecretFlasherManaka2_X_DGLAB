import asyncio
import json
import queue
import sys
import tempfile
import time
from pathlib import Path


CLIENT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CLIENT_DIR))

from client_gui import (  # noqa: E402  # test imports the local client module
    BridgeWorker,
    CLIMAX_OVERLAY_PULSES,
    composite_strength_percent,
    GameUdpProtocol,
    PISTON_OVERLAY_PULSES,
    RuntimeState,
    Settings,
    compose_composite_frame,
    load_pulse_waveforms,
    parse_dungeonlab_pulse,
    piston_display_mode,
    resolve_piston_intensity,
    resolve_vibrator_intensity,
    vibrator_display_mode,
)
from pydglab_ws import Channel, StrengthOperationType


class MockDGLabClient:
    def __init__(self):
        self.strengths = []
        self.cleared = []

    async def set_strength(self, channel, operation, value):
        self.strengths.append((channel, operation, value))

    async def clear_pulses(self, channel):
        self.cleared.append(channel)


class DummyWorker:
    def __init__(self):
        self.state = RuntimeState()
        self.events = queue.Queue()

    def send_event(self, kind, payload=None):
        self.events.put((kind, payload))


def send_packet(protocol, payload):
    data = json.dumps(payload).encode("utf-8")

    async def dispatch():
        protocol.datagram_received(data, ("127.0.0.1", 39090))

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(dispatch())
    else:
        protocol.datagram_received(data, ("127.0.0.1", 39090))


def test_high_effective_mode_is_used_directly():
    dummy = DummyWorker()
    protocol = GameUdpProtocol(dummy)
    send_packet(
        protocol,
        {
            "type": "secretflashermanaka.vibrator",
            "intensity": 70,
            "vibratorOn": True,
            "configuredMode": "Random",
            "effectiveMode": "High",
            "rawStrength": 1,
        },
    )

    settings = Settings(dry_run=True, output_enabled=False, max_coyote_strength=20)
    worker = BridgeWorker(settings, dummy.state, queue.Queue())
    stale, planned, target, actual, _settings, units, *_layers = worker.compute_output(time.monotonic())

    assert stale is False
    assert planned == 70
    assert target == 14
    assert actual == 0
    assert units == [14]


def test_off_mode_zeroes_output():
    dummy = DummyWorker()
    protocol = GameUdpProtocol(dummy)
    send_packet(
        protocol,
        {
            "type": "secretflashermanaka.vibrator",
            "intensity": 0,
            "vibratorOn": False,
            "configuredMode": "Off",
            "effectiveMode": "Off",
            "rawStrength": 0,
        },
    )

    worker = BridgeWorker(Settings(), dummy.state, queue.Queue())
    _stale, _planned, target, _actual, _settings, units, *_layers = worker.compute_output(time.monotonic())

    assert target == 0
    assert units == []


def test_climax_bar_scales_two_game_layers():
    dummy = DummyWorker()
    protocol = GameUdpProtocol(dummy)
    send_packet(
        protocol,
        {
            "type": "secretflashermanaka.vibrator",
            "intensity": 70,
            "vibratorOn": True,
            "configuredMode": "High",
            "effectiveMode": "High",
            "climaxPercent": 50,
            "hasClimaxData": True,
            "climaxActive": False,
            "pistonOn": True,
            "pistonIntensity": 50,
            "hasPistonData": True,
        },
    )

    worker = BridgeWorker(Settings(dry_run=True, max_coyote_strength=20), dummy.state, queue.Queue())
    (
        _stale,
        planned,
        target,
        _actual,
        _settings,
        _units,
        _climax,
        _active,
        _piston,
        vibrator,
        piston,
        *_rest,
    ) = worker.compute_output(time.monotonic())

    assert planned == 78.5
    assert target == 16
    assert vibrator == 14
    assert piston == 10


def test_weighted_composite_examples():
    assert composite_strength_percent(70, 70, 50, False, True, True) == 80.5
    assert composite_strength_percent(30, 50, 50, False, False, False) == 63.0
    assert composite_strength_percent(70, 70, 0, True, True, True) == 90.0
    assert composite_strength_percent(0, 0, 0, False, False, False) == 0.0
    assert composite_strength_percent(0, 0, 50, False, False, False) == 7.0
    assert composite_strength_percent(0, 0, 0, True, False, False) == 90.0


def test_configured_intensity_values_follow_effective_modes():
    settings = Settings(
        vibrator_weak_percent=20,
        vibrator_strong_percent=80,
        piston_weak_percent=25,
        piston_medium_percent=55,
        piston_strong_percent=85,
    )

    assert resolve_vibrator_intensity(settings, "Random", "Low", True, 99) == 20
    assert resolve_vibrator_intensity(settings, "Random", "High", True, 1) == 80
    assert resolve_piston_intensity(settings, 1, 4, True, 99) == 25
    assert resolve_piston_intensity(settings, 2, 4, True, 99) == 55
    assert resolve_piston_intensity(settings, 3, 4, True, 1) == 85


def test_custom_climax_range_and_active_intensity():
    assert composite_strength_percent(70, 0, 50, False, True, False, 60, 95) == 73.0
    assert composite_strength_percent(70, 0, 50, True, True, False, 60, 95) == 95.0


def test_f8_disarms_all_output_layers():
    dummy = DummyWorker()
    protocol = GameUdpProtocol(dummy)
    send_packet(
        protocol,
        {
            "type": "secretflashermanaka.vibrator",
            "armed": False,
            "intensity": 70,
            "vibratorOn": True,
            "configuredMode": "High",
            "effectiveMode": "High",
            "pistonOn": True,
            "pistonConfiguredMode": 3,
            "pistonEffectiveMode": 3,
            "pistonIntensity": 70,
            "hasPistonData": True,
            "climaxPercent": 100,
            "climaxActive": True,
            "hasClimaxData": True,
        },
    )

    worker = BridgeWorker(Settings(dry_run=False, output_enabled=True), dummy.state, queue.Queue())
    _stale, planned, target, _actual, _settings, _units, climax, active, *_rest = worker.compute_output(time.monotonic())

    assert planned == 0.0
    assert target == 0
    assert climax == 0.0
    assert active is False


def test_f12_emits_panic_event():
    dummy = DummyWorker()
    protocol = GameUdpProtocol(dummy)
    send_packet(protocol, {"type": "secretflashermanaka.vibrator", "panic": True, "reason": "panic"})

    assert dummy.events.get_nowait() == ("panic", "F12")


def test_output_without_custom_waveform_keeps_direct_strength():
    dummy = DummyWorker()
    protocol = GameUdpProtocol(dummy)
    send_packet(
        protocol,
        {
            "type": "secretflashermanaka.vibrator",
            "intensity": 70,
            "vibratorOn": True,
            "configuredMode": "High",
            "effectiveMode": "High",
            "hasClimaxData": False,
            "hasPistonData": False,
        },
    )

    settings = Settings(dry_run=False, output_enabled=True, max_coyote_strength=20)
    worker = BridgeWorker(settings, dummy.state, queue.Queue())
    _stale, _planned, target, actual, _settings, _units, *_layers = worker.compute_output(time.monotonic())

    assert target == 14
    assert actual == 14


def test_mode_display_text_is_compact_and_localized():
    assert vibrator_display_mode("Off", "Off", False) == "关"
    assert vibrator_display_mode("Low", "Low", True) == "弱"
    assert vibrator_display_mode("High", "High", True) == "强"
    assert vibrator_display_mode("Random", "Low", True) == "弱(随机)"
    assert vibrator_display_mode("Random", "High", True) == "强(随机)"
    assert vibrator_display_mode("RandomMode", "High", True) == "强(随机)"
    assert vibrator_display_mode("随机", "Low", True) == "弱(随机)"
    assert piston_display_mode(0, 0, False, False) == "关"
    assert piston_display_mode(1, 1, True, False) == "弱"
    assert piston_display_mode(2, 2, True, False) == "中"
    assert piston_display_mode(3, 3, True, False) == "强"
    assert piston_display_mode(4, 1, True, True) == "弱"
    assert piston_display_mode(4, 2, True, True) == "中"
    assert piston_display_mode(4, 3, True, True) == "强"


def test_disabling_climax_wave_removes_bar_and_active_override():
    dummy = DummyWorker()
    protocol = GameUdpProtocol(dummy)
    send_packet(
        protocol,
        {
            "type": "secretflashermanaka.vibrator",
            "intensity": 70,
            "vibratorOn": True,
            "vibratorStrong": True,
            "effectiveMode": "High",
            "climaxPercent": 50,
            "climaxActive": True,
            "pistonOn": True,
            "pistonIntensity": 50,
            "hasPistonData": True,
        },
    )

    worker = BridgeWorker(Settings(climax_wave_enabled=False, max_coyote_strength=20), dummy.state, queue.Queue())
    _stale, planned, _target, _actual, _settings, _units, climax, active, *_rest = worker.compute_output(time.monotonic())

    assert planned == 75.0
    assert climax == 0.0
    assert active is False


def test_composite_frame_contains_three_wave_layers():
    frame, peak = compose_composite_frame(
        [
            ([((30, 30, 30, 30), (100, 0, 0, 0))], 70, 0.0, "vibrator", True),
            (PISTON_OVERLAY_PULSES, 70, 0.05, "piston", True),
            (CLIMAX_OVERLAY_PULSES, 50, 0.1, "climax", False),
        ],
        20,
        0.0,
    )

    assert peak > 0
    assert any(value > 0 for value in frame[1])


def test_active_climax_is_a_real_composite_layer():
    frame, peak = compose_composite_frame(
        [
            (CLIMAX_OVERLAY_PULSES, 100, 0.1, "climax", False),
        ],
        20,
        0.0,
        climax_active=True,
    )

    assert peak == 18
    assert all(value == 90 for value in frame[1])


def test_layer_epoch_starts_each_layer_at_its_first_frame():
    pulses = [
        ((10, 10, 10, 10), (100, 100, 100, 100)),
        ((10, 10, 10, 10), (0, 0, 0, 0)),
    ]
    at_activation, _ = compose_composite_frame(
        [(pulses, 70, 0.0, "vibrator", False, 5.0)],
        20,
        5.0,
    )
    one_frame_later, _ = compose_composite_frame(
        [(pulses, 70, 0.0, "vibrator", False, 5.0)],
        20,
        5.115,
    )

    assert all(value == 70 for value in at_activation[1])
    assert all(value == 0 for value in one_frame_later[1])


def test_layer_activation_epochs_are_independent_and_queue_cursor_advances():
    worker = BridgeWorker(Settings(), RuntimeState(), queue.Queue())
    worker.pulse_time = 2.0
    worker.update_layer_epochs(["vibrator"])
    assert worker.layer_epochs["vibrator"] == 2.0
    assert worker.layer_reset_pending is True

    worker.layer_reset_pending = False
    worker.pulse_time = 3.0
    worker.update_layer_epochs(["vibrator", "piston"])
    assert worker.layer_epochs["vibrator"] == 2.0
    assert worker.layer_epochs["piston"] == 3.0
    assert worker.layer_reset_pending is True

    worker.current_composite_layers = [
        ([((10, 10, 10, 10), (100, 100, 100, 100))], 70, 0.0, "vibrator", False, 2.0),
    ]
    worker.next_pulse_chunk(Settings(send_hz=5), count=2)
    assert worker.pulse_time == 3.2


def test_dungeonlab_pulse_sections_are_converted_to_client_frames():
    text = (
        "Dungeonlab+pulse:5,1,16=0,16,0,1,1/"
        "0.00-1,20.00-0,40.00-0,60.00-0,80.00-0,100.00-1"
        "+section+28,0,11,3,1/100.00-1,100.00-0,100.00-0,100.00-1"
        "+section+0,20,29,1,1/100.00-1,100.00-1"
        "+section+0,20,20,1,0/0.00-1,20.00-1"
    )
    pulses = parse_dungeonlab_pulse(text)

    assert len(pulses) == 49
    assert pulses[0] == ((10, 10, 10, 10), (0, 0, 0, 0))
    assert pulses[1][1] == (0, 0, 0, 0)
    assert pulses[6][1] == (100, 100, 100, 100)
    assert pulses[7][0] == (26, 26, 26, 26)
    assert pulses[8][0] == (32, 32, 32, 32)
    assert pulses[-1] == ((100, 100, 100, 100), (100, 100, 100, 100))


def test_pulse_folder_loader_uses_file_stem_and_skips_bad_files():
    with tempfile.TemporaryDirectory() as directory:
        tmp_path = Path(directory)
        (tmp_path / "环 开始.pulse").write_text(
            "Dungeonlab+pulse:0,1,1=0,20,0,1,1/25.00-1",
            encoding="utf-8",
        )
        (tmp_path / "坏文件.pulse").write_text("not a waveform", encoding="utf-8")

        records = load_pulse_waveforms(tmp_path)

    assert [record["name"] for record in records] == ["环 开始"]
    assert records[0]["source"] == "pulse-file:环 开始.pulse"
    assert records[0]["pulses"][0][1] == (25, 25, 25, 25)


def test_send_strength_both_channels_with_multiplier():
    settings = Settings(dry_run=False, output_enabled=True, channel="Both", channel_b_multiplier=2.5)
    worker = BridgeWorker(settings, RuntimeState(), queue.Queue())
    client = MockDGLabClient()
    asyncio.run(worker.send_strength(client, 10))
    assert client.strengths == [
        (Channel.A, StrengthOperationType.SET_TO, 10),
        (Channel.B, StrengthOperationType.SET_TO, 25),
    ]


def test_send_strength_clamps_at_200():
    settings = Settings(dry_run=False, output_enabled=True, channel="Both", channel_b_multiplier=10.0)
    worker = BridgeWorker(settings, RuntimeState(), queue.Queue())
    client = MockDGLabClient()
    asyncio.run(worker.send_strength(client, 30))
    assert client.strengths == [
        (Channel.A, StrengthOperationType.SET_TO, 30),
        (Channel.B, StrengthOperationType.SET_TO, 200),
    ]


def test_send_strength_single_channel_ignores_multiplier():
    settings_a = Settings(dry_run=False, output_enabled=True, channel="A", channel_b_multiplier=3.0)
    worker_a = BridgeWorker(settings_a, RuntimeState(), queue.Queue())
    client_a = MockDGLabClient()
    asyncio.run(worker_a.send_strength(client_a, 15))
    assert client_a.strengths == [
        (Channel.A, StrengthOperationType.SET_TO, 15),
    ]

    settings_b = Settings(dry_run=False, output_enabled=True, channel="B", channel_b_multiplier=3.0)
    worker_b = BridgeWorker(settings_b, RuntimeState(), queue.Queue())
    client_b = MockDGLabClient()
    asyncio.run(worker_b.send_strength(client_b, 15))
    assert client_b.strengths == [
        (Channel.B, StrengthOperationType.SET_TO, 15),
    ]


def test_safe_zero_clears_both_channels_unconditionally():
    settings = Settings(dry_run=True, output_enabled=True, channel="A")
    state = RuntimeState()
    state.app_bound = True
    worker = BridgeWorker(settings, state, queue.Queue())
    client = MockDGLabClient()
    asyncio.run(worker.safe_zero(client))
    assert (Channel.A, StrengthOperationType.SET_TO, 0) in client.strengths
    assert (Channel.B, StrengthOperationType.SET_TO, 0) in client.strengths
    assert Channel.A in client.cleared
    assert Channel.B in client.cleared


def test_channel_b_multiplier_default_and_settings():
    s = Settings()
    assert s.channel_b_multiplier == 1.0


def test_output_loop_channel_switch_and_multiplier_immediate_send():
    settings = Settings(
        dry_run=False,
        output_enabled=True,
        channel="A",
        channel_b_multiplier=1.0,
        max_coyote_strength=50,
        ramp_units_per_second=10000,
    )
    state = RuntimeState()
    state.app_bound = True
    state.game_armed = True
    state.direct_vibrator = True
    state.vibrator_on = True
    state.intensity_percent = 100.0
    state.last_udp_time = time.time()
    worker = BridgeWorker(settings, state, queue.Queue())
    worker.current_units = 50.0
    client = MockDGLabClient()

    async def run_scenario():
        task = asyncio.create_task(worker.output_loop(client))
        await asyncio.sleep(0.06)
        assert (Channel.A, StrengthOperationType.SET_TO, 50) in client.strengths
        client.strengths.clear()

        # Switch A -> Both without changing strength: immediate send without waiting 1.0s
        settings.channel = "Both"
        await asyncio.sleep(0.06)
        assert (Channel.A, StrengthOperationType.SET_TO, 50) in client.strengths
        assert (Channel.B, StrengthOperationType.SET_TO, 50) in client.strengths
        client.strengths.clear()

        # Multiplier change on Both triggers immediate send with clamped output
        settings.channel_b_multiplier = 2.0
        await asyncio.sleep(0.06)
        assert (Channel.A, StrengthOperationType.SET_TO, 50) in client.strengths
        assert (Channel.B, StrengthOperationType.SET_TO, 100) in client.strengths
        client.strengths.clear()

        # Switch Both -> A: dropped channel B is cleared immediately
        settings.channel = "A"
        await asyncio.sleep(0.06)
        assert (Channel.B, StrengthOperationType.SET_TO, 0) in client.strengths
        assert Channel.B in client.cleared
        assert (Channel.A, StrengthOperationType.SET_TO, 50) in client.strengths

        worker.stop_flag.set()
        await task

    asyncio.run(run_scenario())


if __name__ == "__main__":
    test_high_effective_mode_is_used_directly()
    test_off_mode_zeroes_output()
    test_climax_bar_scales_two_game_layers()
    test_weighted_composite_examples()
    test_output_without_custom_waveform_keeps_direct_strength()
    test_mode_display_text_is_compact_and_localized()
    test_disabling_climax_wave_removes_bar_and_active_override()
    test_composite_frame_contains_three_wave_layers()
    test_active_climax_is_a_real_composite_layer()
    test_layer_epoch_starts_each_layer_at_its_first_frame()
    test_layer_activation_epochs_are_independent_and_queue_cursor_advances()
    test_dungeonlab_pulse_sections_are_converted_to_client_frames()
    test_output_loop_channel_switch_and_multiplier_immediate_send()
    print("vibrator bridge tests: PASS")
