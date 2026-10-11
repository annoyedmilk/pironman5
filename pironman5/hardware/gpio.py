"""One GPIO output pin through rpi.lgpio, the RPi.GPIO drop-in for the Pi 5."""

from __future__ import annotations

from typing import Any

from ..logger import get_logger

log = get_logger("gpio")


class OutputPin:
    """A digital output pin, BCM numbering."""

    def __init__(self, number: int) -> None:
        from RPi import GPIO

        self.number = number
        self._gpio = GPIO
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        GPIO.setup(number, GPIO.OUT)
        GPIO.output(number, GPIO.LOW)

    def set(self, on: bool) -> None:
        self._gpio.output(self.number, int(on))

    def close(self) -> None:
        # No cleanup(): it makes the pin an input, and the fans run when the pin is not low.
        self.set(False)


class NullPin:
    def __init__(self, number: int) -> None:
        self.number = number

    def set(self, on: bool) -> None:
        pass

    def close(self) -> None:
        pass


def open_pin(number: int, mock: bool) -> Any:
    if mock:
        return NullPin(number)
    try:
        return OutputPin(number)
    except Exception as exc:  # noqa: BLE001, the GPIO backend raises its own exception types
        log.warning("GPIO unavailable (%s) - pin %d has no effect", exc, number)
        return NullPin(number)
