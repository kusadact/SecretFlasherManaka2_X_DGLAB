import asyncio
import json
import queue
import sys
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
CLIENT_DIR = REPO_ROOT / "client"
sys.path.insert(0, str(CLIENT_DIR))

from client_gui import BridgeWorker, GameUdpProtocol, RuntimeState, Settings  # noqa: E402  # test imports the local client module


class FixtureWorker:
    def __init__(self, settings):
        self.settings = settings
        self.state = RuntimeState()
        self.events = queue.Queue()

    def send_event(self, kind, payload=None):
        self.events.put((kind, payload))


def load_fixture(name):
    path = REPO_ROOT / "tests" / "fixtures" / name
    return json.loads(path.read_text(encoding="utf-8"))


def send_payload(worker, payload):
    protocol = GameUdpProtocol(worker)

    async def dispatch():
        protocol.datagram_received(
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            ("127.0.0.1", 39090),
        )

    asyncio.run(dispatch())


def send_bytes(worker, data):
    protocol = GameUdpProtocol(worker)

    async def dispatch():
        protocol.datagram_received(data, ("127.0.0.1", 39090))

    asyncio.run(dispatch())


def test_random_fixture_uses_effective_vibrator_and_piston_modes():
    settings = Settings(
        vibrator_weak_percent=20,
        vibrator_strong_percent=80,
        piston_weak_percent=25,
        piston_medium_percent=55,
        piston_strong_percent=85,
        climax_range_percent=60,
    )
    worker = FixtureWorker(settings)
    send_payload(worker, load_fixture("vibrator_random.json"))

    assert worker.state.intensity_percent == 20
    assert worker.state.piston_intensity_percent == 85
    assert worker.state.piston_strong is True

    bridge = BridgeWorker(settings, worker.state, queue.Queue())
    _stale, planned, target, _actual, *_rest = bridge.compute_output(time.monotonic())
    assert planned == 90.0
    assert target == 18


def test_f8_fixture_forces_zero_output():
    settings = Settings(dry_run=False, output_enabled=True)
    worker = FixtureWorker(settings)
    send_payload(worker, load_fixture("pause_f8.json"))

    bridge = BridgeWorker(settings, worker.state, queue.Queue())
    _stale, planned, target, _actual, *_rest = bridge.compute_output(time.monotonic())
    assert worker.state.game_armed is False
    assert planned == 0.0
    assert target == 0


def test_f12_fixture_emits_panic_event():
    worker = FixtureWorker(Settings())
    send_payload(worker, load_fixture("panic_f12.json"))

    assert worker.events.get_nowait() == ("panic", "F12")


def test_malformed_json_emits_parse_error():
    worker = FixtureWorker(Settings())
    send_bytes(worker, b"{not-json")

    kind, message = worker.events.get_nowait()
    assert kind == "error"
    assert "UDP 数据解析失败" in message
