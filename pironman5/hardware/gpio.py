"""GPIO pin helper on top of ``rpi.lgpio`` (the lgpio-backed RPi.GPIO drop-in).

On the Pi 5 the legacy RPi.GPIO C extension does not work (RP1 I/O controller);
``rpi.lgpio`` provides the same API over lgpio. We program against the RPi.GPIO
interface so this stays a thin, swappable shim. Falls back to mock when the lib
isn't importable.
"""

from __future__ import annotations

from ..logger import get_logger

log = get_logger("gpio")


class OutputPin:
    """A single digital output pin (BCM numbering)."""

    def __init__(self, pin: int, mock: bool = False) -> None:
        self.pin = pin
        self.mock = mock
        self._value = 0
        self._gpio = None
        if not mock:
            self._open()

    def _open(self) -> None:
        try:
            # rpi.lgpio installs itself as the RPi.GPIO module.
            from RPi import GPIO  # type: ignore

            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)
            GPIO.setup(self.pin, GPIO.OUT)
            GPIO.output(self.pin, GPIO.LOW)
            self._gpio = GPIO
            log.debug("opened GPIO pin %d", self.pin)
        except Exception as exc:
            log.warning("GPIO unavailable (%s) - pin %d running mock", exc, self.pin)
            self.mock = True

    @property
    def value(self) -> int:
        return self._value

    def set(self, value: bool) -> None:
        self._value = 1 if value else 0
        if not self.mock and self._gpio is not None:
            self._gpio.output(self.pin, self._value)

    def on(self) -> None:
        self.set(True)

    def off(self) -> None:
        self.set(False)

    def change_pin(self, pin: int) -> None:
        """Reassign to a different BCM pin (used when fan.gpio_pin changes)."""
        self.close()
        self.pin = pin
        self._value = 0
        if not self.mock:
            self._open()

    def close(self) -> None:
        try:
            self.off()
            if not self.mock and self._gpio is not None:
                self._gpio.cleanup(self.pin)
        except Exception:  # pragma: no cover - teardown best effort
            pass
