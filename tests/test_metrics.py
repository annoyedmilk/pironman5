from pironman5.metrics import MockBoard, Sampler


def test_sample_has_each_interface_and_the_connected_one_first():
    sample = Sampler(MockBoard()).read()
    wifi, wired = sample.interfaces
    assert (wifi.name, wifi.wireless, wifi.up, wifi.signal) == ("wlan0", True, True, -57)
    assert (wired.name, wired.wireless, wired.up, wired.ip) == ("eth0", False, False, None)
    assert wired.mac and sample.os and sample.firmware
