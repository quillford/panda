#!/usr/bin/env python3
import unittest
import numpy as np
from panda import Panda
from panda.tests.safety import libpandasafety_py
import panda.tests.safety.common as common
from panda.tests.safety.common import make_msg, twos_comp, UNSAFE_MODE

MAX_RATE_UP = 50
MAX_RATE_DOWN = 70
MAX_STEER = 2047

MAX_RT_DELTA = 940
RT_INTERVAL = 250000

DRIVER_TORQUE_ALLOWANCE = 60
DRIVER_TORQUE_FACTOR = 10


def subaru_checksum(msg, addr, len_msg):
  checksum = addr + (addr >> 8)
  for i in range(len_msg):
    if i < 4:
      checksum += (msg.RDLR >> (8 * i))
    else:
      checksum += (msg.RDHR >> (8 * (i - 4)))
  return checksum & 0xff


class TestSubaruSafety(common.PandaSafetyTest):
  cnt_gas = 0
  cnt_torque_driver = 0
  cnt_cruise = 0
  cnt_speed = 0
  cnt_brake = 0

  TX_MSGS = [[0x122, 0], [0x221, 0], [0x322, 0]]
  STANDSTILL_THRESHOLD = 20  # 1kph (see dbc file)
  RELAY_MALFUNCTION_ADDR = 0x122
  RELAY_MALFUNCTION_BUS = 0
  FWD_BLACKLISTED_ADDRS = {2: [290, 545, 802]}
  FWD_BUS_LOOKUP = {0: 2, 2: 0}

  def setUp(self):
    self.safety = libpandasafety_py.libpandasafety
    self.safety.set_safety_hooks(Panda.SAFETY_SUBARU, 0)
    self.safety.init_tests_subaru()

  def _set_prev_torque(self, t):
    self.safety.set_subaru_desired_torque_last(t)
    self.safety.set_subaru_rt_torque_last(t)

  def _torque_driver_msg(self, torque):
    t = twos_comp(torque, 11)
    if self.safety.get_subaru_global():
      to_send = make_msg(0, 0x119)
      to_send[0].RDLR = ((t & 0x7FF) << 16)
      to_send[0].RDLR |= (self.cnt_torque_driver & 0xF) << 8
      to_send[0].RDLR |= subaru_checksum(to_send, 0x119, 8)
      self.__class__.cnt_torque_driver += 1
    else:
      to_send = make_msg(0, 0x371)
      to_send[0].RDLR = (t & 0x7) << 29
      to_send[0].RDHR = (t >> 3) & 0xFF
    return to_send

  def _speed_msg(self, speed):
    speed &= 0x1FFF
    to_send = make_msg(0, 0x13a)
    to_send[0].RDLR = speed << 12
    to_send[0].RDHR = speed << 6
    to_send[0].RDLR |= (self.cnt_speed & 0xF) << 8
    to_send[0].RDLR |= subaru_checksum(to_send, 0x13a, 8)
    self.__class__.cnt_speed += 1
    return to_send

  def _brake_msg(self, brake):
    to_send = make_msg(0, 0x139)
    to_send[0].RDHR = (brake << 4) & 0xFFF
    to_send[0].RDLR |= (self.cnt_brake & 0xF) << 8
    to_send[0].RDLR |= subaru_checksum(to_send, 0x139, 8)
    self.__class__.cnt_brake += 1
    return to_send

  def _torque_msg(self, torque):
    t = twos_comp(torque, 13)
    if self.safety.get_subaru_global():
      to_send = make_msg(0, 0x122)
      to_send[0].RDLR = (t << 16)
    else:
      to_send = make_msg(0, 0x164)
      to_send[0].RDLR = (t << 8)
    return to_send

  def _gas_msg(self, gas):
    if self.safety.get_subaru_global():
      to_send = make_msg(0, 0x40)
      to_send[0].RDHR = gas & 0xFF
      to_send[0].RDLR |= (self.cnt_gas & 0xF) << 8
      to_send[0].RDLR |= subaru_checksum(to_send, 0x40, 8)
      self.__class__.cnt_gas += 1
    else:
      to_send = make_msg(0, 0x140)
      to_send[0].RDLR = gas & 0xFF
    return to_send

  def _pcm_status_msg(self, cruise):
    if self.safety.get_subaru_global():
      to_send = make_msg(0, 0x240)
      to_send[0].RDHR = cruise << 9
      to_send[0].RDLR |= (self.cnt_cruise & 0xF) << 8
      to_send[0].RDLR |= subaru_checksum(to_send, 0x240, 8)
      self.__class__.cnt_cruise += 1
    else:
      to_send = make_msg(0, 0x144)
      to_send[0].RDHR = cruise << 17
    return to_send

  def _set_torque_driver(self, min_t, max_t):
    for i in range(0, 5):
      self.safety.safety_rx_hook(self._torque_driver_msg(min_t))
    self.safety.safety_rx_hook(self._torque_driver_msg(max_t))

  def test_steer_safety_check(self):
    for enabled in [0, 1]:
      for t in range(-3000, 3000):
        self.safety.set_controls_allowed(enabled)
        self._set_prev_torque(t)
        if abs(t) > MAX_STEER or (not enabled and abs(t) > 0):
          self.assertFalse(self.safety.safety_tx_hook(self._torque_msg(t)))
        else:
          self.assertTrue(self.safety.safety_tx_hook(self._torque_msg(t)))

  def test_non_realtime_limit_up(self):
    self._set_torque_driver(0, 0)
    self.safety.set_controls_allowed(True)

    self._set_prev_torque(0)
    self.assertTrue(self.safety.safety_tx_hook(self._torque_msg(MAX_RATE_UP)))
    self._set_prev_torque(0)
    self.assertTrue(self.safety.safety_tx_hook(self._torque_msg(-MAX_RATE_UP)))

    self._set_prev_torque(0)
    self.assertFalse(self.safety.safety_tx_hook(self._torque_msg(MAX_RATE_UP + 1)))
    self.safety.set_controls_allowed(True)
    self._set_prev_torque(0)
    self.assertFalse(self.safety.safety_tx_hook(self._torque_msg(-MAX_RATE_UP - 1)))

  def test_non_realtime_limit_down(self):
    self._set_torque_driver(0, 0)
    self.safety.set_controls_allowed(True)

  def test_against_torque_driver(self):
    self.safety.set_controls_allowed(True)

    for sign in [-1, 1]:
      for t in np.arange(0, DRIVER_TORQUE_ALLOWANCE + 1, 1):
        t *= -sign
        self._set_torque_driver(t, t)
        self._set_prev_torque(MAX_STEER * sign)
        self.assertTrue(self.safety.safety_tx_hook(self._torque_msg(MAX_STEER * sign)))

      self._set_torque_driver(DRIVER_TORQUE_ALLOWANCE + 1, DRIVER_TORQUE_ALLOWANCE + 1)
      self.assertFalse(self.safety.safety_tx_hook(self._torque_msg(-MAX_STEER)))

    # arbitrary high driver torque to ensure max steer torque is allowed
    max_driver_torque = int(MAX_STEER / DRIVER_TORQUE_FACTOR + DRIVER_TORQUE_ALLOWANCE + 1)

    # spot check some individual cases
    for sign in [-1, 1]:
      driver_torque = (DRIVER_TORQUE_ALLOWANCE + 10) * sign
      torque_desired = (MAX_STEER - 10 * DRIVER_TORQUE_FACTOR) * sign
      delta = 1 * sign
      self._set_prev_torque(torque_desired)
      self._set_torque_driver(-driver_torque, -driver_torque)
      self.assertTrue(self.safety.safety_tx_hook(self._torque_msg(torque_desired)))
      self._set_prev_torque(torque_desired + delta)
      self._set_torque_driver(-driver_torque, -driver_torque)
      self.assertFalse(self.safety.safety_tx_hook(self._torque_msg(torque_desired + delta)))

      self._set_prev_torque(MAX_STEER * sign)
      self._set_torque_driver(-max_driver_torque * sign, -max_driver_torque * sign)
      self.assertTrue(self.safety.safety_tx_hook(self._torque_msg((MAX_STEER - MAX_RATE_DOWN) * sign)))
      self._set_prev_torque(MAX_STEER * sign)
      self._set_torque_driver(-max_driver_torque * sign, -max_driver_torque * sign)
      self.assertTrue(self.safety.safety_tx_hook(self._torque_msg(0)))
      self._set_prev_torque(MAX_STEER * sign)
      self._set_torque_driver(-max_driver_torque * sign, -max_driver_torque * sign)
      self.assertFalse(self.safety.safety_tx_hook(self._torque_msg((MAX_STEER - MAX_RATE_DOWN + 1) * sign)))


  def test_realtime_limits(self):
    self.safety.set_controls_allowed(True)

    for sign in [-1, 1]:
      self.safety.init_tests_subaru()
      self._set_prev_torque(0)
      self._set_torque_driver(0, 0)
      for t in np.arange(0, MAX_RT_DELTA, 1):
        t *= sign
        self.assertTrue(self.safety.safety_tx_hook(self._torque_msg(t)))
      self.assertFalse(self.safety.safety_tx_hook(self._torque_msg(sign * (MAX_RT_DELTA + 1))))

      self._set_prev_torque(0)
      for t in np.arange(0, MAX_RT_DELTA, 1):
        t *= sign
        self.assertTrue(self.safety.safety_tx_hook(self._torque_msg(t)))

      # Increase timer to update rt_torque_last
      self.safety.set_timer(RT_INTERVAL + 1)
      self.assertTrue(self.safety.safety_tx_hook(self._torque_msg(sign * (MAX_RT_DELTA - 1))))
      self.assertTrue(self.safety.safety_tx_hook(self._torque_msg(sign * (MAX_RT_DELTA + 1))))


class TestSubaruLegacySafety(TestSubaruSafety):

  TX_MSGS = [[0x164, 0], [0x221, 0], [0x322, 0]]
  RELAY_MALFUNCTION_ADDR = 0x164
  RELAY_MALFUNCTION_BUS = 0
  FWD_BLACKLISTED_ADDRS = {2: [356, 545, 802]}
  FWD_BUS_LOOKUP = {0: 2, 2: 0}

  def setUp(self):
    self.safety = libpandasafety_py.libpandasafety
    self.safety.set_safety_hooks(Panda.SAFETY_SUBARU_LEGACY, 0)
    self.safety.init_tests_subaru()

  def _torque_driver_msg(self, torque):
    t = twos_comp(torque, 11)
    to_send = make_msg(0, 0x371)
    to_send[0].RDLR = (t & 0x7) << 29
    to_send[0].RDHR = (t >> 3) & 0xFF
    return to_send

  def _torque_msg(self, torque):
    t = twos_comp(torque, 13)
    to_send = make_msg(0, 0x164)
    to_send[0].RDLR = (t << 8)
    return to_send

  def _gas_msg(self, gas):
    to_send = make_msg(0, 0x140)
    to_send[0].RDLR = gas & 0xFF
    return to_send

  def _pcm_status_msg(self, cruise):
    to_send = make_msg(0, 0x144)
    to_send[0].RDHR = cruise << 17
    return to_send


if __name__ == "__main__":
  unittest.main()
