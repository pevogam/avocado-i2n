#!/usr/bin/env python

import unittest
import unittest.mock as mock
import os

from avocado.core import exceptions
from avocado.utils import process
from avocado.utils import lv_utils
from virttest import utils_params

import unittest_importer
from avocado_i2n import state_setup


@mock.patch('avocado_i2n.state_setup.os.mkdir', mock.Mock(return_value=0))
@mock.patch('avocado_i2n.state_setup.os.rmdir', mock.Mock(return_value=0))
@mock.patch('avocado_i2n.state_setup.os.unlink', mock.Mock(return_value=0))
@mock.patch('avocado_i2n.state_setup.env_process', mock.Mock(return_value=0))
class StateSetupTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.run_str = ""

    def setUp(self):
        self.run_params = utils_params.Params()
        self.run_params["vms"] = "vm1"
        self.run_params["images"] = "image1"

        self.env = mock.MagicMock(name='env')
        self.env.get_vm = mock.MagicMock(side_effect=self._get_mock_vm)

        exists_patch = mock.patch('avocado_i2n.state_setup.os.path.exists', mock.MagicMock(side_effect=self._file_exists))
        exists_patch.start()
        self.addCleanup(exists_patch.stop)

        self.mock_vms = {}

        self.exist_switch = True

        state_setup.off = state_setup.LVMBackend
        state_setup.on = state_setup.QCOW2Backend

    def _set_off_lvm_params(self):
        self.run_params["vg_name_vm1"] = "ramdisk_vm1"
        self.run_params["lv_name"] = "LogVol"
        self.run_params["lv_pointer_name"] = "current_state"

    def _set_on_qcow2_params(self):
        self.run_params["image_name_vm1"] = "/vm1/image"
        self.run_params["image_format"] = "qcow2"
        self.run_params["qemu_img_binary"] = "qemu-img"

    def _set_on_ramfile_params(self):
        self.run_params["image_name_vm1"] = "/vm1/image"

    def _get_mock_vm(self, vm_name):
        return self.mock_vms[vm_name]

    def _create_mock_vms(self):
        for vm_name in self.run_params.objects("vms"):
            self.mock_vms[vm_name] = mock.MagicMock(name=vm_name)
            self.mock_vms[vm_name].name = vm_name
            self.mock_vms[vm_name].params = self.run_params.object_params(vm_name)

    def _file_exists(self, filepath):
        return self.exist_switch

    @mock.patch('avocado_i2n.state_setup.lv_utils')
    def test_show_states_off(self, mock_lv_utils):
        self._set_off_lvm_params()
        self.run_params["check_type_vm1"] = "off"
        self._create_mock_vms()

        mock_lv_utils.lv_list.return_value = ["launch1", "launch2"]
        states = state_setup.show_states(self.run_params, self.env)
        mock_lv_utils.lv_list.assert_called_once_with("ramdisk_vm1")

        self.assertIn("launch1", states)
        self.assertIn("launch2", states)
        self.assertNotIn("launch3", states)
        self.assertNotIn("root", states)

    @mock.patch('avocado_i2n.state_setup.process')
    def test_show_states_on(self, mock_process):
        self._set_on_qcow2_params()
        self.run_params["check_type_vm1"] = "on"
        self._create_mock_vms()

        mock_process.reset_mock()
        mock_process.system_output.return_value = b"NOT HERE"
        states = state_setup.show_states(self.run_params, self.env)
        mock_process.system_output.assert_called_once_with("qemu-img snapshot -l /vm1/image.qcow2 -U")

        self.assertEqual(len(states), 0)

        mock_process.reset_mock()
        mock_process.system_output.return_value = (b"1         launch   338M 2014-05-16 12:13:45   00:00:34.079"
                                                   b"2         with.dot   33.5G 2014-05-16 12:13:45   00:00:34.079")
        states = state_setup.show_states(self.run_params, self.env)
        mock_process.system_output.assert_called_once_with("qemu-img snapshot -l /vm1/image.qcow2 -U")
 
        self.assertEqual(len(states), 2)
        self.assertIn("launch", states)
        self.assertIn("with.dot", states)
        self.assertNotIn("launch2", states)
        self.assertNotIn("boot", states)

    @mock.patch('avocado_i2n.state_setup.glob')
    def test_show_states_on_ramfile(self, mock_glob):
        self._set_on_ramfile_params()
        self.run_params["check_type_vm1"] = "on"
        self._create_mock_vms()
        state_setup.on = state_setup.RamfileBackend

        mock_glob.reset_mock()
        mock_glob.glob.return_value = []
        states = state_setup.show_states(self.run_params, self.env)
        mock_glob.glob.assert_called_once_with("/vm1/*.state")

        self.assertEqual(len(states), 0)

        mock_glob.reset_mock()
        mock_glob.glob.return_value = ["/vm1/launch.state", "/vm1/with.dot.state"]
        states = state_setup.show_states(self.run_params, self.env)
        mock_glob.glob.assert_called_once_with("/vm1/*.state")

        self.assertEqual(len(states), 2)
        self.assertIn("/vm1/launch.state", states)
        self.assertIn("/vm1/with.dot.state", states)
        self.assertNotIn("/vm1/launch2", states)
        self.assertNotIn("boot", states)

    @mock.patch('avocado_i2n.state_setup.lv_utils')
    def test_check_off(self, mock_lv_utils):
        self._set_off_lvm_params()
        self.run_params["check_state_vm1"] = "launch"
        self.run_params["check_type_vm1"] = "off"
        self._create_mock_vms()

        mock_lv_utils.reset_mock()
        mock_lv_utils.lv_check.return_value = True
        exists = state_setup.check_state(self.run_params, self.env)
        mock_lv_utils.lv_check.assert_called_once_with("ramdisk_vm1", "launch")
        self.assertTrue(exists)

        mock_lv_utils.reset_mock()
        mock_lv_utils.lv_check.return_value = False
        exists = state_setup.check_state(self.run_params, self.env)
        mock_lv_utils.lv_check.assert_called_once_with("ramdisk_vm1", "launch")
        self.assertFalse(exists)

    @mock.patch('avocado_i2n.state_setup.process')
    def test_check_on(self, mock_process):
        self._set_on_qcow2_params()
        self.run_params["check_state_vm1"] = "launch"
        self.run_params["check_type_vm1"] = "on"
        self._create_mock_vms()

        mock_process.reset_mock()
        mock_process.system_output.return_value = b"NOT HERE"
        exists = state_setup.check_state(self.run_params, self.env)
        mock_process.system_output.assert_called_once_with("qemu-img snapshot -l /vm1/image.qcow2 -U")
        self.assertFalse(exists)

        mock_process.reset_mock()
        mock_process.system_output.return_value = b"1         launch   338M 2014-05-16 12:13:45   00:00:34.079"
        exists = state_setup.check_state(self.run_params, self.env)
        mock_process.system_output.assert_called_once_with("qemu-img snapshot -l /vm1/image.qcow2 -U")
        self.assertTrue(exists)

    @mock.patch('avocado_i2n.state_setup.os')
    def test_check_on_ramfile(self, mock_os):
        self._set_on_ramfile_params()
        self.run_params["check_state_vm1"] = "launch"
        self.run_params["check_type_vm1"] = "on"
        self._create_mock_vms()
        state_setup.on = state_setup.RamfileBackend

        # restore some unmocked parts of the os module
        mock_os.path.dirname = os.path.dirname
        mock_os.path.join = os.path.join
        # we cannot use the exist switch because we also want to assert calls
        # mock_os.path.exists = self._file_exists

        mock_os.reset_mock()
        mock_os.path.exists.return_value = False
        exists = state_setup.check_state(self.run_params, self.env)
        mock_os.path.exists.assert_called_once_with("/vm1/launch.state")
        self.assertFalse(exists)

        mock_os.reset_mock()
        mock_os.path.exists.return_value = True
        exists = state_setup.check_state(self.run_params, self.env)
        mock_os.path.exists.assert_called_once_with("/vm1/launch.state")
        self.assertTrue(exists)

    @mock.patch('avocado_i2n.state_setup.process')
    def test_check_on_dot(self, mock_process):
        self._set_on_qcow2_params()
        self.run_params["check_state_vm1"] = "with.dot"
        self.run_params["check_type_vm1"] = "on"
        self._create_mock_vms()

        mock_process.system_output.return_value = b"1         with.dot   33.5G 2014-05-16 12:13:45   00:00:34.079"
        exists = state_setup.check_state(self.run_params, self.env)
        mock_process.system_output.assert_called_once_with("qemu-img snapshot -l /vm1/image.qcow2 -U")
        self.assertTrue(exists)

    @mock.patch('avocado_i2n.state_setup.process')
    @mock.patch('avocado_i2n.state_setup.lv_utils')
    def test_check_any_all(self, _mock_lv_utils, mock_process):
        self._set_on_qcow2_params()
        self.run_params["check_state_vm1"] = "launch"
        self._create_mock_vms()

        mock_process.system_output.return_value = b"1         launch   338M 2014-05-16 12:13:45   00:00:34.079"
        exists = state_setup.check_state(self.run_params, self.env)
        mock_process.system_output.assert_called_once_with("qemu-img snapshot -l /vm1/image.qcow2 -U")
        self.assertTrue(exists)

    @mock.patch('avocado_i2n.state_setup.process')
    @mock.patch('avocado_i2n.state_setup.lv_utils')
    def test_check_any_fallback(self, mock_lv_utils, mock_process):
        self._set_on_qcow2_params()
        self._set_off_lvm_params()
        self.run_params["check_state_vm1"] = "launch"
        self._create_mock_vms()

        mock_process.system_output.return_value = b"NOT HERE"
        mock_lv_utils.lv_check.return_value = True
        exists = state_setup.check_state(self.run_params, self.env)
        mock_process.system_output.assert_called_once_with("qemu-img snapshot -l /vm1/image.qcow2 -U")
        mock_lv_utils.lv_check.assert_called_once_with("ramdisk_vm1", "launch")
        self.assertTrue(exists)

    @mock.patch('avocado_i2n.state_setup.process')
    @mock.patch('avocado_i2n.state_setup.lv_utils')
    def test_check_any_none(self, mock_lv_utils, mock_process):
        self._set_on_qcow2_params()
        self._set_off_lvm_params()
        self.run_params["check_state_vm1"] = "launch"
        self._create_mock_vms()

        mock_lv_utils.reset_mock()
        mock_process.reset_mock()
        mock_process.system_output.return_value = b"NOT HERE"
        mock_lv_utils.lv_check.return_value = False
        exists = state_setup.check_state(self.run_params, self.env)
        mock_process.system_output.assert_called_once_with("qemu-img snapshot -l /vm1/image.qcow2 -U")
        mock_lv_utils.lv_check.assert_called_once_with("ramdisk_vm1", "launch")
        self.assertFalse(exists)

    @mock.patch('avocado_i2n.state_setup.lv_utils')
    def test_get_off_aa(self, mock_lv_utils):
        self._set_off_lvm_params()
        self.run_params["get_state_vm1"] = "launch"
        self.run_params["get_type_vm1"] = "off"
        self.run_params["get_mode_vm1"] = "aa"
        self._create_mock_vms()

        mock_lv_utils.reset_mock()
        mock_lv_utils.lv_check.return_value = True
        self.mock_vms["vm1"].is_alive.return_value = False
        with self.assertRaises(exceptions.TestSkipError):
            state_setup.get_state(self.run_params, self.env)
        mock_lv_utils.lv_check.assert_called_once_with("ramdisk_vm1", "launch")
        # test on/off switch as well
        self.mock_vms["vm1"].is_alive.assert_called_once_with()

        mock_lv_utils.reset_mock()
        mock_lv_utils.lv_check.return_value = False
        with self.assertRaises(exceptions.TestSkipError):
            state_setup.get_state(self.run_params, self.env)
        mock_lv_utils.lv_check.assert_called_once_with("ramdisk_vm1", "launch")

    @mock.patch('avocado_i2n.state_setup.lv_utils')
    def test_get_off_rx(self, mock_lv_utils):
        self._set_off_lvm_params()
        self.run_params["get_state_vm1"] = "launch"
        self.run_params["get_type_vm1"] = "off"
        self.run_params["get_mode_vm1"] = "rx"
        self.run_params["image_name_vm1"] = "/vm1/image"
        self.run_params["image_raw_device_vm1"] = "no"
        self._create_mock_vms()

        mock_lv_utils.reset_mock()
        # test on/off switch as well
        mock_lv_utils.lv_check.return_value = True
        self.mock_vms["vm1"].is_alive.return_value = False
        state_setup.get_state(self.run_params, self.env)
        mock_lv_utils.lv_check.assert_called_once_with("ramdisk_vm1", "launch")
        self.mock_vms["vm1"].is_alive.assert_called_once_with()
        mock_lv_utils.lv_remove.assert_called_once_with('ramdisk_vm1', 'current_state')
        mock_lv_utils.lv_take_snapshot.assert_called_once_with('ramdisk_vm1', 'launch', 'current_state')

        mock_lv_utils.reset_mock()
        mock_lv_utils.lv_check.return_value = False
        with self.assertRaises(exceptions.TestError):
            state_setup.get_state(self.run_params, self.env)
        mock_lv_utils.lv_check.assert_called_once_with("ramdisk_vm1", "launch")

    @mock.patch('avocado_i2n.state_setup.lv_utils')
    def test_get_off_ii(self, mock_lv_utils):
        self._set_off_lvm_params()
        self.run_params["get_state_vm1"] = "launch"
        self.run_params["get_type_vm1"] = "off"
        self.run_params["get_mode_vm1"] = "ii"
        self._create_mock_vms()

        mock_lv_utils.reset_mock()
        mock_lv_utils.lv_check.return_value = True
        self.mock_vms["vm1"].is_alive.return_value = False
        state_setup.get_state(self.run_params, self.env)
        mock_lv_utils.lv_check.assert_called_once_with("ramdisk_vm1", "launch")
        self.mock_vms["vm1"].is_alive.assert_called_once_with()

        mock_lv_utils.reset_mock()
        mock_lv_utils.lv_check.return_value = False
        state_setup.get_state(self.run_params, self.env)
        mock_lv_utils.lv_check.assert_called_once_with("ramdisk_vm1", "launch")

    @mock.patch('avocado_i2n.state_setup.lv_utils')
    def test_get_off_xx(self, mock_lv_utils):
        self._set_off_lvm_params()
        self.run_params["get_state_vm1"] = "launch"
        self.run_params["get_type_vm1"] = "off"
        self.run_params["get_mode_vm1"] = "xx"
        self._create_mock_vms()

        mock_lv_utils.reset_mock()
        mock_lv_utils.lv_check.return_value = True
        self.mock_vms["vm1"].is_alive.return_value = False
        with self.assertRaises(exceptions.TestError):
            state_setup.get_state(self.run_params, self.env)
        mock_lv_utils.lv_check.assert_called_once_with("ramdisk_vm1", "launch")
        self.mock_vms["vm1"].is_alive.assert_called_once_with()

        mock_lv_utils.reset_mock()
        mock_lv_utils.lv_check.return_value = False
        with self.assertRaises(exceptions.TestError):
            state_setup.get_state(self.run_params, self.env)
        mock_lv_utils.lv_check.assert_called_once_with("ramdisk_vm1", "launch")

    @mock.patch('avocado_i2n.state_setup.lv_utils')
    def test_get_type_switch(self, mock_lv_utils):
        self._set_off_lvm_params()
        self.run_params["get_state_vm1"] = "launch"
        self.run_params["get_type_vm1"] = "off"
        self.run_params["get_mode_vm1"] = "ii"
        self._create_mock_vms()

        mock_lv_utils.reset_mock()
        self.mock_vms["vm1"].reset_mock()
        mock_lv_utils.lv_check.return_value = True
        self.mock_vms["vm1"].is_alive.return_value = True
        state_setup.get_state(self.run_params, self.env)
        mock_lv_utils.lv_check.assert_called_once_with("ramdisk_vm1", "launch")
        self.mock_vms["vm1"].is_alive.assert_called_once_with()
        self.mock_vms["vm1"].destroy.assert_called_once_with(gracefully=False)

        mock_lv_utils.reset_mock()
        self.mock_vms["vm1"].reset_mock()
        mock_lv_utils.lv_check.return_value = True
        self.mock_vms["vm1"].is_alive.return_value = False
        state_setup.get_state(self.run_params, self.env)
        mock_lv_utils.lv_check.assert_called_once_with("ramdisk_vm1", "launch")
        self.mock_vms["vm1"].is_alive.assert_called_once_with()

    @mock.patch('avocado_i2n.state_setup.process')
    def test_get_on_rx(self, mock_process):
        self._set_on_qcow2_params()
        self.run_params["get_state_vm1"] = "launch"
        self.run_params["get_type_vm1"] = "on"
        self.run_params["get_mode_vm1"] = "rx"
        self._create_mock_vms()

        mock_process.system_output.return_value = b"1         launch   338M 2014-05-16 12:13:45   00:00:34.079"
        self.mock_vms["vm1"].is_alive.return_value = True
        # switch check if vm has to be booted
        state_setup.get_state(self.run_params, self.env)
        mock_process.system_output.assert_called_once_with("qemu-img snapshot -l /vm1/image.qcow2 -U")
        self.mock_vms["vm1"].is_alive.assert_called_once_with()
        self.mock_vms["vm1"].loadvm.assert_called_once_with('launch')

    @mock.patch('avocado_i2n.state_setup.os')
    def test_get_on_ramfile(self, mock_os):
        self._set_on_ramfile_params()
        self.run_params["get_state_vm1"] = "launch"
        self.run_params["get_type_vm1"] = "on"
        self.run_params["get_mode_vm1"] = "rx"
        self._create_mock_vms()
        state_setup.on = state_setup.RamfileBackend

        # restore some unmocked parts of the os module
        mock_os.path.dirname = os.path.dirname
        mock_os.path.join = os.path.join
        # we cannot use the exist switch because we also want to assert calls
        # mock_os.path.exists = self._file_exists

        mock_os.reset_mock()
        mock_os.path.exists.return_value = True
        state_setup.get_state(self.run_params, self.env)
        self.mock_vms["vm1"].restore_from_file.assert_called_once_with("/vm1/launch.state")

    @mock.patch('avocado_i2n.state_setup.process')
    @mock.patch('avocado_i2n.state_setup.lv_utils')
    def test_get_any_all_rx(self, mock_lv_utils, mock_process):
        self._set_on_qcow2_params()
        self.run_params["get_state_vm1"] = "launch"
        self.run_params["get_mode_vm1"] = "rx"
        self._create_mock_vms()

        # if >= 1 states prefer on
        mock_process.system_output.return_value = b"1         launch   338M 2014-05-16 12:13:45   00:00:34.079"
        # this time the on switch asks so confirm for it as well
        self.mock_vms["vm1"].is_alive.return_value = True
        state_setup.get_state(self.run_params, self.env)
        mock_process.system_output.assert_called_once_with("qemu-img snapshot -l /vm1/image.qcow2 -U")
        self.mock_vms["vm1"].is_alive.assert_called_once_with()
        self.mock_vms["vm1"].loadvm.assert_called_once_with('launch')

    @mock.patch('avocado_i2n.state_setup.process')
    @mock.patch('avocado_i2n.state_setup.lv_utils')
    def test_get_any_fallback_rx(self, mock_lv_utils, mock_process):
        self._set_on_qcow2_params()
        self._set_off_lvm_params()
        self.run_params["get_state_vm1"] = "launch"
        self.run_params["get_mode_vm1"] = "rx"
        self.run_params["image_raw_device_vm1"] = "no"
        self._create_mock_vms()

        # if only off state choose it
        mock_process.system_output.return_value = b"NOT HERE"
        mock_lv_utils.lv_check.return_value = True
        # this time the off switch asks so confirm for it as well
        self.mock_vms["vm1"].is_alive.return_value = False
        state_setup.get_state(self.run_params, self.env)
        mock_process.system_output.assert_called_once_with("qemu-img snapshot -l /vm1/image.qcow2 -U")
        mock_lv_utils.lv_check.assert_called_once_with("ramdisk_vm1", "launch")
        self.mock_vms["vm1"].is_alive.assert_called_once_with()
        mock_lv_utils.lv_remove.assert_called_once_with('ramdisk_vm1', 'current_state')
        mock_lv_utils.lv_take_snapshot.assert_called_once_with('ramdisk_vm1', 'launch', 'current_state')

    @mock.patch('avocado_i2n.state_setup.process')
    @mock.patch('avocado_i2n.state_setup.lv_utils')
    def test_get_any_none_xi(self, mock_lv_utils, mock_process):
        self._set_on_qcow2_params()
        self._set_off_lvm_params()
        self.run_params["get_state_vm1"] = "launch"
        self.run_params["get_mode_vm1"] = "xi"
        # self.run_params["image_raw_device_vm1"] = "no"
        self._create_mock_vms()

        # if no states prefer on
        mock_process.system_output.return_value = b"NOT HERE"
        mock_lv_utils.lv_check.return_value = False
        state_setup.get_state(self.run_params, self.env)
        mock_process.system_output.assert_called_once_with("qemu-img snapshot -l /vm1/image.qcow2 -U")
        mock_lv_utils.lv_check.assert_called_once_with("ramdisk_vm1", "launch")

    @mock.patch('avocado_i2n.state_setup.lv_utils')
    def test_set_off_aa(self, mock_lv_utils):
        self._set_off_lvm_params()
        self.run_params["set_state_vm1"] = "launch"
        self.run_params["set_type_vm1"] = "off"
        self.run_params["set_mode_vm1"] = "aa"
        self.run_params["skip_types"] = "on"
        self._create_mock_vms()

        mock_lv_utils.reset_mock()
        self.mock_vms["vm1"].reset_mock()
        mock_lv_utils.lv_check.return_value = True
        with self.assertRaises(exceptions.TestSkipError):
            state_setup.set_state(self.run_params, self.env)
        mock_lv_utils.lv_check.assert_called_once_with("ramdisk_vm1", "launch")
        self.mock_vms["vm1"].destroy.assert_called_once_with(gracefully=True)

        mock_lv_utils.reset_mock()
        self.mock_vms["vm1"].reset_mock()
        mock_lv_utils.lv_check.return_value = False
        with self.assertRaises(exceptions.TestSkipError):
            state_setup.set_state(self.run_params, self.env)
        mock_lv_utils.lv_check.assert_called_once_with("ramdisk_vm1", "launch")
        self.mock_vms["vm1"].destroy.assert_called_once_with(gracefully=True)

    @mock.patch('avocado_i2n.state_setup.lv_utils')
    def test_set_off_rx(self, mock_lv_utils):
        self._set_off_lvm_params()
        self.run_params["set_state_vm1"] = "launch"
        self.run_params["set_type_vm1"] = "off"
        self.run_params["set_mode_vm1"] = "rx"
        self.run_params["skip_types"] = "on"
        self._create_mock_vms()

        mock_lv_utils.reset_mock()
        self.mock_vms["vm1"].reset_mock()
        mock_lv_utils.lv_check.return_value = True
        state_setup.set_state(self.run_params, self.env)
        mock_lv_utils.lv_check.assert_called_once_with("ramdisk_vm1", "launch")
        self.mock_vms["vm1"].destroy.assert_called_once_with(gracefully=True)

        mock_lv_utils.reset_mock()
        self.mock_vms["vm1"].reset_mock()
        mock_lv_utils.lv_check.return_value = False
        with self.assertRaises(exceptions.TestError):
            state_setup.set_state(self.run_params, self.env)
        mock_lv_utils.lv_check.assert_called_once_with("ramdisk_vm1", "launch")
        self.mock_vms["vm1"].destroy.assert_called_once_with(gracefully=True)

    @mock.patch('avocado_i2n.state_setup.lv_utils')
    def test_set_off_ff(self, mock_lv_utils):
        self._set_off_lvm_params()
        self.run_params["set_state_vm1"] = "launch"
        self.run_params["set_type_vm1"] = "off"
        self.run_params["set_mode_vm1"] = "ff"
        self.run_params["skip_types"] = "on"
        self._create_mock_vms()

        mock_lv_utils.reset_mock()
        self.mock_vms["vm1"].reset_mock()
        mock_lv_utils.lv_check.return_value = True
        state_setup.set_state(self.run_params, self.env)
        mock_lv_utils.lv_check.assert_called_once_with("ramdisk_vm1", "launch")
        self.mock_vms["vm1"].destroy.assert_called_once_with(gracefully=True)
        mock_lv_utils.lv_remove.assert_called_once_with("ramdisk_vm1", "launch")
        mock_lv_utils.lv_take_snapshot.assert_called_once_with('ramdisk_vm1', 'current_state', 'launch')

        mock_lv_utils.reset_mock()
        self.mock_vms["vm1"].reset_mock()
        mock_lv_utils.lv_check.return_value = False
        state_setup.set_state(self.run_params, self.env)
        mock_lv_utils.lv_check.assert_called_once_with("ramdisk_vm1", "launch")
        self.mock_vms["vm1"].destroy.assert_called_once_with(gracefully=True)
        mock_lv_utils.lv_take_snapshot.assert_called_once_with('ramdisk_vm1', 'current_state', 'launch')

    @mock.patch('avocado_i2n.state_setup.lv_utils')
    def test_set_off_xx(self, mock_lv_utils):
        self._set_off_lvm_params()
        self.run_params["set_state_vm1"] = "launch"
        self.run_params["set_type_vm1"] = "off"
        self.run_params["set_mode_vm1"] = "xx"
        self.run_params["skip_types"] = "on"
        self._create_mock_vms()

        mock_lv_utils.reset_mock()
        self.mock_vms["vm1"].reset_mock()
        mock_lv_utils.lv_check.return_value = True
        with self.assertRaises(exceptions.TestError):
            state_setup.set_state(self.run_params, self.env)
        mock_lv_utils.lv_check.assert_called_once_with("ramdisk_vm1", "launch")
        self.mock_vms["vm1"].destroy.assert_called_once_with(gracefully=True)

        mock_lv_utils.reset_mock()
        self.mock_vms["vm1"].reset_mock()
        mock_lv_utils.lv_check.return_value = False
        with self.assertRaises(exceptions.TestError):
            state_setup.set_state(self.run_params, self.env)
        mock_lv_utils.lv_check.assert_called_once_with("ramdisk_vm1", "launch")
        self.mock_vms["vm1"].destroy.assert_called_once_with(gracefully=True)

    @mock.patch('avocado_i2n.state_setup.process')
    def test_set_on_ff(self, mock_process):
        self._set_on_qcow2_params()
        self.run_params["set_state_vm1"] = "launch"
        self.run_params["set_type_vm1"] = "on"
        self.run_params["set_mode_vm1"] = "ff"
        self.run_params["skip_types"] = "off"
        self._create_mock_vms()

        # NOTE: setting an on state assumes that the vm is on just like
        # setting an off state assumes that the vm already exists
        mock_process.system_output.return_value = b"1         launch   338M 2014-05-16 12:13:45   00:00:34.079"
        state_setup.set_state(self.run_params, self.env)
        mock_process.system_output.assert_called_once_with("qemu-img snapshot -l /vm1/image.qcow2 -U")
        self.mock_vms["vm1"].savevm.assert_called_once_with('launch')

    @mock.patch('avocado_i2n.state_setup.os')
    def test_set_on_ramfile(self, mock_os):
        self._set_on_ramfile_params()
        self.run_params["set_state_vm1"] = "launch"
        self.run_params["set_type_vm1"] = "on"
        self.run_params["set_mode_vm1"] = "ff"
        self.run_params["skip_types"] = "off"
        self._create_mock_vms()
        state_setup.on = state_setup.RamfileBackend

        # restore some unmocked parts of the os module
        mock_os.path.dirname = os.path.dirname
        mock_os.path.join = os.path.join
        # we cannot use the exist switch because we also want to assert calls
        # mock_os.path.exists = self._file_exists

        mock_os.reset_mock()
        mock_os.path.exists.return_value = True
        state_setup.set_state(self.run_params, self.env)
        self.mock_vms["vm1"].save_to_file.assert_called_once_with("/vm1/launch.state")

    @mock.patch('avocado_i2n.state_setup.process')
    @mock.patch('avocado_i2n.state_setup.lv_utils')
    def test_set_any_all_ff(self, mock_lv_utils, mock_process):
        self._set_on_qcow2_params()
        self.run_params["set_state_vm1"] = "launch"
        self.run_params["set_mode_vm1"] = "ff"
        self.run_params["skip_types"] = ""
        self._create_mock_vms()

        # if no skipping and too many states prefer on
        mock_process.system_output.return_value = b"1         launch   338M 2014-05-16 12:13:45   00:00:34.079"
        state_setup.set_state(self.run_params, self.env)
        mock_process.system_output.assert_called_once_with("qemu-img snapshot -l /vm1/image.qcow2 -U")
        self.mock_vms["vm1"].savevm.assert_called_once_with('launch')

    @mock.patch('avocado_i2n.state_setup.process')
    @mock.patch('avocado_i2n.state_setup.lv_utils')
    def test_set_any_fallback_ff(self, mock_lv_utils, mock_process):
        self._set_on_qcow2_params()
        self._set_off_lvm_params()
        self.run_params["set_state_vm1"] = "launch"
        self.run_params["set_mode_vm1"] = "ff"
        self.run_params["skip_types"] = ""
        self._create_mock_vms()

        # if no skipping with only off state available
        mock_process.system_output.return_value = b"NOT HERE"
        mock_lv_utils.lv_check.return_value = True
        state_setup.set_state(self.run_params, self.env)
        mock_process.system_output.assert_called_once_with("qemu-img snapshot -l /vm1/image.qcow2 -U")
        mock_lv_utils.lv_check.assert_called_once_with("ramdisk_vm1", "launch")
        self.mock_vms["vm1"].destroy.assert_called_once_with(gracefully=True)
        mock_lv_utils.lv_remove.assert_called_once_with('ramdisk_vm1', 'launch')
        mock_lv_utils.lv_take_snapshot.assert_called_once_with('ramdisk_vm1', 'current_state', 'launch')

    @mock.patch('avocado_i2n.state_setup.process')
    @mock.patch('avocado_i2n.state_setup.lv_utils')
    def test_set_any_none_ff(self, mock_lv_utils, mock_process):
        self._set_on_qcow2_params()
        self._set_off_lvm_params()
        self.run_params["set_state_vm1"] = "launch"
        self.run_params["set_mode_vm1"] = "ff"
        self.run_params["skip_types"] = ""
        self._create_mock_vms()

        # if no skipping and no states prefer on
        mock_process.system_output.return_value = b"NOT HERE"
        mock_lv_utils.lv_check.return_value = False
        state_setup.set_state(self.run_params, self.env)
        mock_process.system_output.assert_called_once_with("qemu-img snapshot -l /vm1/image.qcow2 -U")
        mock_lv_utils.lv_check.assert_called_once_with("ramdisk_vm1", "launch")
        self.mock_vms["vm1"].savevm.assert_called_once_with('launch')

    @mock.patch('avocado_i2n.state_setup.process')
    @mock.patch('avocado_i2n.state_setup.lv_utils')
    def test_set_any_all_skip_on(self, mock_lv_utils, mock_process):
        self._set_on_qcow2_params()
        self.run_params["set_state_vm1"] = "launch"
        self.run_params["set_mode_vm1"] = "ff"
        self.run_params["skip_types"] = "on"
        self._create_mock_vms()

        # skip setting the state since on is available but we skip on by parameters
        mock_process.system_output.return_value = b"1         launch   338M 2014-05-16 12:13:45   00:00:34.079"
        state_setup.set_state(self.run_params, self.env)
        mock_process.system_output.assert_called_once_with("qemu-img snapshot -l /vm1/image.qcow2 -U")

    @mock.patch('avocado_i2n.state_setup.process')
    @mock.patch('avocado_i2n.state_setup.lv_utils')
    def test_set_any_fallback_skip_on(self, mock_lv_utils, mock_process):
        self._set_on_qcow2_params()
        self._set_off_lvm_params()
        self.run_params["set_state_vm1"] = "launch"
        self.run_params["set_mode_vm1"] = "ff"
        self.run_params["skip_types"] = "on"
        self._create_mock_vms()

        # set the state since only off is available and we skip on by parameters
        mock_process.system_output.return_value = b"NOT HERE"
        mock_lv_utils.lv_check.return_value = True
        state_setup.set_state(self.run_params, self.env)
        mock_process.system_output.assert_called_once_with("qemu-img snapshot -l /vm1/image.qcow2 -U")
        mock_lv_utils.lv_check.assert_called_once_with("ramdisk_vm1", "launch")
        self.mock_vms["vm1"].destroy.assert_called_once_with(gracefully=True)
        mock_lv_utils.lv_remove.assert_called_once_with("ramdisk_vm1", "launch")
        mock_lv_utils.lv_take_snapshot.assert_called_once_with('ramdisk_vm1', 'current_state', 'launch')

    @mock.patch('avocado_i2n.state_setup.process')
    @mock.patch('avocado_i2n.state_setup.lv_utils')
    def test_set_any_fallback_skip_off(self, mock_lv_utils, mock_process):
        self._set_on_qcow2_params()
        self._set_off_lvm_params()
        self.run_params["set_state_vm1"] = "launch"
        self.run_params["set_mode_vm1"] = "ff"
        self.run_params["skip_types"] = "off"
        self._create_mock_vms()

        # skip setting the state since only off is available but we skip off by parameters
        mock_process.system_output.return_value = b"NOT HERE"
        mock_lv_utils.lv_check.return_value = True
        state_setup.set_state(self.run_params, self.env)
        mock_process.system_output.assert_called_once_with("qemu-img snapshot -l /vm1/image.qcow2 -U")
        mock_lv_utils.lv_check.assert_called_once_with("ramdisk_vm1", "launch")

    @mock.patch('avocado_i2n.state_setup.lv_utils')
    def test_unset_off_ra(self, mock_lv_utils):
        self._set_off_lvm_params()
        self.run_params["unset_state_vm1"] = "launch"
        self.run_params["unset_type_vm1"] = "off"
        self.run_params["unset_mode_vm1"] = "ra"
        self._create_mock_vms()

        mock_lv_utils.reset_mock()
        mock_lv_utils.lv_check.return_value = True
        state_setup.unset_state(self.run_params, self.env)
        mock_lv_utils.lv_check.assert_called_once_with("ramdisk_vm1", "launch")

        mock_lv_utils.reset_mock()
        mock_lv_utils.lv_check.return_value = False
        with self.assertRaises(exceptions.TestSkipError):
            state_setup.unset_state(self.run_params, self.env)
        mock_lv_utils.lv_check.assert_called_once_with("ramdisk_vm1", "launch")

    @mock.patch('avocado_i2n.state_setup.lv_utils')
    def test_unset_off_fi(self, mock_lv_utils):
        self._set_off_lvm_params()
        self.run_params["unset_state_vm1"] = "launch"
        self.run_params["unset_type_vm1"] = "off"
        self.run_params["unset_mode_vm1"] = "fi"
        self.run_params["lv_pointer_name"] = "current_state"
        self._create_mock_vms()

        mock_lv_utils.reset_mock()
        mock_lv_utils.lv_check.return_value = True
        state_setup.unset_state(self.run_params, self.env)
        mock_lv_utils.lv_check.assert_called_once_with("ramdisk_vm1", "launch")
        mock_lv_utils.lv_remove.assert_called_once_with("ramdisk_vm1", "launch")

        mock_lv_utils.reset_mock()
        mock_lv_utils.lv_check.return_value = False
        state_setup.unset_state(self.run_params, self.env)
        mock_lv_utils.lv_check.assert_called_once_with("ramdisk_vm1", "launch")

    @mock.patch('avocado_i2n.state_setup.lv_utils')
    def test_unset_off_xx(self, mock_lv_utils):
        self._set_off_lvm_params()
        self.run_params["unset_state_vm1"] = "launch"
        self.run_params["unset_type_vm1"] = "off"
        self.run_params["unset_mode_vm1"] = "xx"
        self._create_mock_vms()

        mock_lv_utils.reset_mock()
        mock_lv_utils.lv_check.return_value = True
        with self.assertRaises(exceptions.TestError):
            state_setup.unset_state(self.run_params, self.env)
        mock_lv_utils.lv_check.assert_called_once_with("ramdisk_vm1", "launch")

        mock_lv_utils.reset_mock()
        mock_lv_utils.lv_check.return_value = False
        with self.assertRaises(exceptions.TestError):
            state_setup.unset_state(self.run_params, self.env)
        mock_lv_utils.lv_check.assert_called_once_with("ramdisk_vm1", "launch")

    @mock.patch('avocado_i2n.state_setup.process')
    def test_unset_on_fi(self, mock_process):
        self._set_on_qcow2_params()
        self.run_params["unset_state_vm1"] = "launch"
        self.run_params["unset_type_vm1"] = "on"
        self.run_params["unset_mode_vm1"] = "fi"
        self._create_mock_vms()

        mock_process.system_output.return_value = b"1         launch   338M 2014-05-16 12:13:45   00:00:34.079"
        self.mock_vms["vm1"].monitor.send_args_cmd.return_value = ""
        state_setup.unset_state(self.run_params, self.env)
        mock_process.system_output.assert_called_once_with("qemu-img snapshot -l /vm1/image.qcow2 -U")
        self.mock_vms["vm1"].monitor.send_args_cmd.assert_called_once_with("delvm id=launch")

    @mock.patch('avocado_i2n.state_setup.os')
    def test_unset_on_ramfile(self, mock_os):
        self._set_on_ramfile_params()
        self.run_params["unset_state_vm1"] = "launch"
        self.run_params["unset_type_vm1"] = "on"
        self.run_params["unset_mode_vm1"] = "fi"
        self._create_mock_vms()
        state_setup.on = state_setup.RamfileBackend

        # restore some unmocked parts of the os module
        mock_os.path.dirname = os.path.dirname
        mock_os.path.join = os.path.join
        # we cannot use the exist switch because we also want to assert calls
        # mock_os.path.exists = self._file_exists

        mock_os.reset_mock()
        mock_os.path.exists.return_value = True
        state_setup.unset_state(self.run_params, self.env)
        mock_os.unlink.assert_called_once_with("/vm1/launch.state")

    @mock.patch('avocado_i2n.state_setup.process')
    @mock.patch('avocado_i2n.state_setup.lv_utils')
    def test_unset_any_all_fi(self, _mock_lv_utils, mock_process):
        self._set_on_qcow2_params()
        self.run_params["unset_state_vm1"] = "launch"
        self.run_params["unset_mode_vm1"] = "fi"
        self._create_mock_vms()

        # if >= 1 states prefer on
        mock_process.system_output.return_value = b"1         launch   338M 2014-05-16 12:13:45   00:00:34.079"
        self.mock_vms["vm1"].monitor.send_args_cmd.return_value = ""
        state_setup.unset_state(self.run_params, self.env)
        mock_process.system_output.assert_called_once_with("qemu-img snapshot -l /vm1/image.qcow2 -U")
        self.mock_vms["vm1"].monitor.send_args_cmd.assert_called_once_with("delvm id=launch")

    @mock.patch('avocado_i2n.state_setup.process')
    @mock.patch('avocado_i2n.state_setup.lv_utils')
    def test_unset_any_fallback_fi(self, mock_lv_utils, mock_process):
        self._set_on_qcow2_params()
        self._set_off_lvm_params()
        self.run_params["unset_state_vm1"] = "launch"
        self.run_params["unset_mode_vm1"] = "fi"
        self.run_params["lv_pointer_name"] = "current_state"
        self._create_mock_vms()

        # if only off state choose it
        mock_process.system_output.return_value = b"NOT HERE"
        mock_lv_utils.lv_check.return_value = True
        state_setup.unset_state(self.run_params, self.env)
        mock_process.system_output.assert_called_once_with("qemu-img snapshot -l /vm1/image.qcow2 -U")
        mock_lv_utils.lv_check.assert_called_once_with("ramdisk_vm1", "launch")
        mock_lv_utils.lv_remove.assert_called_once_with("ramdisk_vm1", "launch")

    @mock.patch('avocado_i2n.state_setup.lv_utils')
    def test_unset_keep_pointer(self, mock_lv_utils):
        self._set_on_qcow2_params()
        self._set_off_lvm_params()
        self.run_params["unset_state_vm1"] = "current_state"
        self.run_params["unset_type_vm1"] = "off"
        self.run_params["unset_mode_vm1"] = "fi"
        self.run_params["lv_pointer_name"] = "current_state"
        self._create_mock_vms()

        # if only off state choose it
        mock_lv_utils.lv_check.return_value = True
        with self.assertRaises(ValueError):
            state_setup.unset_state(self.run_params, self.env)
        mock_lv_utils.lv_check.assert_called_once_with("ramdisk_vm1", "current_state")

    @mock.patch('avocado_i2n.state_setup.process')
    @mock.patch('avocado_i2n.state_setup.lv_utils')
    def test_unset_any_none_fi(self, mock_lv_utils, mock_process):
        self._set_on_qcow2_params()
        self._set_off_lvm_params()
        self.run_params["unset_state_vm1"] = "launch"
        self.run_params["unset_mode_vm1"] = "fi"
        self._create_mock_vms()

        # if no states cannot do anything
        mock_process.system_output.return_value = b"NOT HERE"
        mock_lv_utils.lv_check.return_value = False
        state_setup.unset_state(self.run_params, self.env)
        mock_process.system_output.assert_called_once_with("qemu-img snapshot -l /vm1/image.qcow2 -U")
        mock_lv_utils.lv_check.assert_called_once_with("ramdisk_vm1", "launch")

    @mock.patch('avocado_i2n.state_setup.lv_utils')
    def test_check_root_off(self, mock_lv_utils):
        self._set_off_lvm_params()
        self.run_params["check_state_vm1"] = "root"
        self.run_params["check_type_vm1"] = "off"
        self._create_mock_vms()

        mock_lv_utils.reset_mock()
        mock_lv_utils.lv_check.return_value = True
        exists = state_setup.check_state(self.run_params, self.env)
        mock_lv_utils.lv_check.assert_called_once_with("ramdisk_vm1", "LogVol")
        self.assertTrue(exists)

        mock_lv_utils.reset_mock()
        mock_lv_utils.lv_check.return_value = False
        exists = state_setup.check_state(self.run_params, self.env)
        mock_lv_utils.lv_check.assert_called_once_with("ramdisk_vm1", "LogVol")
        self.assertFalse(exists)

    def test_check_root_on(self):
        self._set_on_qcow2_params()
        self.run_params["check_state_vm1"] = "root"
        self.run_params["check_type_vm1"] = "on"
        self.run_params["images_vm1"] = "image1 image2"
        self.run_params["image_name_image1_vm1"] = "/vm1/image1"
        self.run_params["image_name_image2_vm1"] = "/vm1/image2"
        self._create_mock_vms()

        self.exist_switch = True
        exists = state_setup.check_state(self.run_params, self.env)
        self.assertTrue(exists)

        self.exist_switch = False
        exists = state_setup.check_state(self.run_params, self.env)
        self.assertFalse(exists)

    def test_check_root_on_ramfile(self):
        self._set_on_ramfile_params()
        self.run_params["check_state_vm1"] = "root"
        self.run_params["check_type_vm1"] = "on"
        self.run_params["images_vm1"] = "image1 image2"
        self.run_params["image_name_image1_vm1"] = "/vm1/image1"
        self.run_params["image_name_image2_vm1"] = "/vm1/image2"
        self._create_mock_vms()
        state_setup.on = state_setup.RamfileBackend

        self.exist_switch = True
        for image_format in ["qcow2", "raw", "something-else"]:
            self.run_params["image_format"] = image_format
            exists = state_setup.check_state(self.run_params, self.env)
            self.assertTrue(exists)

        self.exist_switch = False
        self.run_params["image_format"] = "img"
        exists = state_setup.check_state(self.run_params, self.env)
        self.assertFalse(exists)

    def test_check_boot(self):
        self.run_params["check_state_vm1"] = "boot"
        self.run_params["check_type_vm1"] = "on"
        self._create_mock_vms()

        self.mock_vms["vm1"].reset_mock()
        self.mock_vms["vm1"].is_alive.return_value = True
        exists = state_setup.check_state(self.run_params, self.env)
        self.mock_vms["vm1"].is_alive.assert_called_once_with()
        self.assertTrue(exists)

        self.mock_vms["vm1"].reset_mock()
        self.mock_vms["vm1"].is_alive.return_value = False
        exists = state_setup.check_state(self.run_params, self.env)
        self.mock_vms["vm1"].is_alive.assert_called_once_with()
        self.assertFalse(exists)

    @mock.patch('avocado_i2n.state_setup.lv_utils')
    def test_get_root_off(self, mock_lv_utils):
        self._set_off_lvm_params()
        self.run_params["check_state_vm1"] = "root"
        self.run_params["check_type_vm1"] = "off"
        self._create_mock_vms()

        state_setup.get_state(self.run_params, self.env)

    @mock.patch('avocado_i2n.state_setup.lv_utils')
    def test_get_root_on(self, mock_lv_utils):
        self._set_on_qcow2_params()
        self.run_params["check_state_vm1"] = "root"
        self.run_params["check_type_vm1"] = "on"
        self._create_mock_vms()

        state_setup.get_state(self.run_params, self.env)

    def test_get_root_on_ramfile(self):
        self._set_on_ramfile_params()
        self.run_params["check_state_vm1"] = "root"
        self.run_params["check_type_vm1"] = "on"
        self._create_mock_vms()
        state_setup.on = state_setup.RamfileBackend

        state_setup.get_state(self.run_params, self.env)

    @mock.patch('avocado_i2n.state_setup.lv_utils')
    def test_set_root_off(self, mock_lv_utils):
        self._set_off_lvm_params()
        self.run_params["set_state_vm1"] = "root"
        self.run_params["set_type_vm1"] = "off"
        self.run_params["set_mode_vm1"] = "af"
        self.run_params["set_size_vm1"] = "30G"
        self.run_params["pool_name"] = "thin_pool"
        self.run_params["pool_size"] = "30G"
        self.run_params["lv_size"] = "30G"
        self.run_params["image_name_vm1"] = "/vm1/image"
        self.run_params["ramdisk_sparse_filename_vm1"] = "virtual_hdd_vm1"
        self.run_params["use_tmpfs"] = "yes"
        self.run_params["ramdisk_basedir_vm1"] = "/tmp"
        self.run_params["ramdisk_vg_size_vm1"] = "40000"
        self.run_params["image_raw_device_vm1"] = "no"
        self._create_mock_vms()

        mock_lv_utils.reset_mock()
        mock_lv_utils.lv_check.return_value = False
        state_setup.set_state(self.run_params, self.env)
        mock_lv_utils.lv_check.assert_called_with('ramdisk_vm1', 'LogVol')
        mock_lv_utils.vg_ramdisk.assert_called_once_with(None, 'ramdisk_vm1', '40000', '/tmp', 'virtual_hdd_vm1', True)
        mock_lv_utils.lv_create.assert_called_once_with('ramdisk_vm1', 'LogVol', '30G', pool_name='thin_pool', pool_size='30G')
        mock_lv_utils.lv_take_snapshot.assert_called_once_with('ramdisk_vm1', 'LogVol', 'current_state')

        mock_lv_utils.reset_mock()
        mock_lv_utils.lv_check.return_value = True
        with self.assertRaises(exceptions.TestSkipError):
            state_setup.set_state(self.run_params, self.env)
        mock_lv_utils.lv_check.assert_called_with('ramdisk_vm1', 'LogVol')

        # force create case
        self.run_params["set_mode_vm1"] = "ff"
        mock_lv_utils.reset_mock()
        mock_lv_utils.lv_check.return_value = True
        #mock_lv_utils.vg_check.return_value = True
        state_setup.set_state(self.run_params, self.env)
        mock_lv_utils.lv_check.assert_called_with('ramdisk_vm1', 'LogVol')
        mock_lv_utils.vg_ramdisk_cleanup.assert_called_once_with('virtual_hdd_vm1', '/tmp/ramdisk_vm1', 'ramdisk_vm1', None, True)
        mock_lv_utils.vg_ramdisk.assert_called_once_with(None, 'ramdisk_vm1', '40000', '/tmp', 'virtual_hdd_vm1', True)
        mock_lv_utils.lv_create.assert_called_once_with('ramdisk_vm1', 'LogVol', '30G', pool_name='thin_pool', pool_size='30G')
        mock_lv_utils.lv_take_snapshot.assert_called_once_with('ramdisk_vm1', 'LogVol', 'current_state')

    def test_set_root_on(self):
        self._set_on_qcow2_params()
        self.run_params["set_state_vm1"] = "root"
        self.run_params["set_type_vm1"] = "on"
        self._create_mock_vms()

        state_setup.set_state(self.run_params, self.env)

    def test_set_root_on_ramfile(self):
        self._set_on_ramfile_params()
        self.run_params["set_state_vm1"] = "root"
        self.run_params["set_type_vm1"] = "on"
        self._create_mock_vms()
        state_setup.on = state_setup.RamfileBackend

        state_setup.set_state(self.run_params, self.env)

    @mock.patch('avocado_i2n.state_setup.lv_utils')
    def test_unset_root_off(self, mock_lv_utils):
        self._set_off_lvm_params()
        self.run_params["unset_state_vm1"] = "root"
        self.run_params["unset_type_vm1"] = "off"
        self.run_params["image_name_vm1"] = "/vm1/image"
        self.run_params["ramdisk_sparse_filename_vm1"] = "virtual_hdd_vm1"
        self.run_params["use_tmpfs"] = "yes"
        self.run_params["ramdisk_basedir_vm1"] = "/tmp"
        self.run_params["image_raw_device_vm1"] = "no"
        self._create_mock_vms()

        mock_lv_utils.reset_mock()
        mock_lv_utils.vg_check.return_value = True
        state_setup.unset_state(self.run_params, self.env)
        mock_lv_utils.vg_check.assert_called_once_with('ramdisk_vm1')
        mock_lv_utils.vg_ramdisk_cleanup.assert_called_once_with('virtual_hdd_vm1', '/tmp/ramdisk_vm1', 'ramdisk_vm1', None, True)

        # test tolerance to cleanup errors
        mock_lv_utils.reset_mock()
        mock_lv_utils.vg_check.return_value = True
        mock_lv_utils.vg_ramdisk_cleanup.side_effect = exceptions.TestError("cleanup failed")
        state_setup.unset_state(self.run_params, self.env)
        mock_lv_utils.vg_check.assert_called_once_with('ramdisk_vm1')
        mock_lv_utils.vg_ramdisk_cleanup.assert_called_once_with('virtual_hdd_vm1', '/tmp/ramdisk_vm1', 'ramdisk_vm1', None, True)

    def test_unset_root_on(self):
        self._set_on_qcow2_params()
        self.run_params["unset_state_vm1"] = "root"
        self.run_params["unset_type_vm1"] = "on"
        self._create_mock_vms()

        state_setup.unset_state(self.run_params, self.env)

    def test_unset_root_on_ramfile(self):
        self._set_on_ramfile_params()
        self.run_params["unset_state_vm1"] = "root"
        self.run_params["unset_type_vm1"] = "on"
        self._create_mock_vms()
        state_setup.on = state_setup.RamfileBackend

        state_setup.unset_state(self.run_params, self.env)

    @mock.patch('avocado_i2n.state_setup.process')
    @mock.patch('avocado_i2n.state_setup.lv_utils')
    def test_push(self, mock_lv_utils, _mock_process):
        self._set_off_lvm_params()
        self.run_params["push_state_vm1"] = "launch"
        self.run_params["push_type_vm1"] = "off"
        self.run_params["push_mode_vm1"] = "ff"
        self.run_params["skip_types"] = "on"
        self.run_params["lv_pointer_name"] = "current_state"
        self._create_mock_vms()

        mock_lv_utils.lv_check.return_value = False
        state_setup.push_state(self.run_params, self.env)
        mock_lv_utils.lv_check.assert_called_once_with("ramdisk_vm1", "launch")
        self.mock_vms["vm1"].destroy.assert_called_once_with(gracefully=True)
        mock_lv_utils.lv_take_snapshot.assert_called_once_with('ramdisk_vm1', 'current_state', 'launch')

        # test push disabled for root/boot states
        self.run_params["push_state_vm1"] = "root"
        state_setup.push_state(self.run_params, self.env)
        mock_lv_utils.assert_not_called()
        self.run_params["push_state_vm1"] = "boot"
        state_setup.push_state(self.run_params, self.env)
        mock_lv_utils.assert_not_called()

    @mock.patch('avocado_i2n.state_setup.lv_utils')
    def test_pop_off(self, mock_lv_utils):
        self._set_off_lvm_params()
        self.run_params["pop_state_vm1"] = "launch"
        self.run_params["pop_type_vm1"] = "off"
        self.run_params["image_name_vm1"] = "/vm1/image"
        self.run_params["image_raw_device_vm1"] = "no"
        self._create_mock_vms()

        mock_lv_utils.lv_check.return_value = True
        self.mock_vms["vm1"].is_alive.return_value = False

        state_setup.pop_state(self.run_params, self.env)

        mock_lv_utils.lv_check.assert_called_with("ramdisk_vm1", "launch")
        self.mock_vms["vm1"].is_alive.assert_called_once_with()
        mock_lv_utils.lv_take_snapshot.assert_called_once_with('ramdisk_vm1', 'launch', 'current_state')
        expected = [mock.call('ramdisk_vm1', 'current_state'), mock.call('ramdisk_vm1', 'launch')]
        self.assertListEqual(mock_lv_utils.lv_remove.call_args_list, expected)

        # test pop disabled for root/boot states
        self.run_params["pop_state_vm1"] = "root"
        state_setup.pop_state(self.run_params, self.env)
        mock_lv_utils.assert_not_called()
        self.run_params["pop_state_vm1"] = "boot"
        state_setup.pop_state(self.run_params, self.env)
        mock_lv_utils.assert_not_called()

    @mock.patch('avocado_i2n.state_setup.process')
    def test_pop_on(self, mock_process):
        self._set_on_qcow2_params()
        self.run_params["pop_state_vm1"] = "launch"
        self.run_params["pop_type_vm1"] = "on"
        self._create_mock_vms()

        mock_process.system_output.return_value = b"1         launch   338M 2014-05-16 12:13:45   00:00:34.079"
        self.mock_vms["vm1"].is_alive.return_value = True
        self.mock_vms["vm1"].monitor.send_args_cmd.return_value = ""

        state_setup.pop_state(self.run_params, self.env)

        mock_process.system_output.assert_called_with("qemu-img snapshot -l /vm1/image.qcow2 -U")
        self.mock_vms["vm1"].is_alive.assert_called_once_with()
        self.mock_vms["vm1"].loadvm.assert_called_once_with('launch')
        self.mock_vms["vm1"].monitor.send_args_cmd.assert_called_once_with("delvm id=launch")

    @mock.patch('avocado_i2n.state_setup.process')
    @mock.patch('avocado_i2n.state_setup.lv_utils')
    def test_check_multivm(self, mock_lv_utils, _mock_process):
        self.run_params["vms"] = "vm1 vm2"
        self.run_params["check_state"] = "launch"
        self.run_params["check_state_vm2"] = "launcher"
        self.run_params["check_type"] = "off"
        self.run_params["vg_name_vm1"] = "ramdisk_vm1"
        self.run_params["vg_name_vm2"] = "ramdisk_vm2"
        self._create_mock_vms()

        mock_lv_utils.reset_mock()
        mock_lv_utils.lv_check.return_value = True
        exists = state_setup.check_state(self.run_params, self.env)
        expected = [mock.call("ramdisk_vm1", "launch"), mock.call("ramdisk_vm2", "launcher")]
        self.assertListEqual(mock_lv_utils.lv_check.call_args_list, expected)
        self.assertTrue(exists)

        # break on first false state check
        mock_lv_utils.reset_mock()
        mock_lv_utils.lv_check.return_value = False
        exists = state_setup.check_state(self.run_params, self.env)
        expected = [mock.call("ramdisk_vm1", "launch")]
        self.assertListEqual(mock_lv_utils.lv_check.call_args_list, expected)
        self.assertFalse(exists)

    @mock.patch('avocado_i2n.state_setup.process')
    @mock.patch('avocado_i2n.state_setup.lv_utils')
    def test_get_multivm(self, mock_lv_utils, mock_process):
        self._set_on_qcow2_params()
        self._set_off_lvm_params()
        self.run_params["vms"] = "vm1 vm2 vm3"
        self.run_params["get_state"] = "launch2"
        self.run_params["get_state_vm1"] = "launch1"
        # TODO: restore allowing digits in the state name once the upstream Qemu
        # handles the bug reported at https://bugs.launchpad.net/qemu/+bug/1859989
        #self.run_params["get_state_vm3"] = "launch3"
        self.run_params["get_state_vm3"] = "launchX"
        self.run_params["get_type"] = "off"
        self.run_params["get_type_vm3"] = "on"
        self.run_params["get_mode_vm1"] = "rx"
        self.run_params["get_mode_vm2"] = "ii"
        self.run_params["get_mode_vm3"] = "aa"
        self.run_params["image_name_vm3"] = "/vm3/image"
        self.run_params["vg_name_vm1"] = "ramdisk_vm1"
        self.run_params["vg_name_vm2"] = "ramdisk_vm2"
        self.run_params["vg_name_vm3"] = "ramdisk_vm3"
        self.run_params["image_name_vm1"] = "/vm1/image"
        self.run_params["image_name_vm2"] = "/vm2/image"
        self.run_params["image_raw_device"] = "no"
        self._create_mock_vms()

        # test on/off switch as well
        def lv_check_side_effect(_vgname, lvname):
            return True if lvname == "launch1" else False if lvname == "launch2" else False
        mock_lv_utils.lv_check.side_effect = lv_check_side_effect
        mock_process.system_output.return_value = b"1         launchX   338M 2014-05-16 12:13:45   00:00:34.079"
        self.mock_vms["vm1"].is_alive.return_value = False
        self.mock_vms["vm3"].is_alive.return_value = True

        with self.assertRaises(exceptions.TestSkipError):
            state_setup.get_state(self.run_params, self.env)

        expected = [mock.call("ramdisk_vm1", "launch1"), mock.call("ramdisk_vm2", "launch2")]
        self.assertListEqual(mock_lv_utils.lv_check.call_args_list, expected)
        mock_process.system_output.assert_called_once_with("qemu-img snapshot -l /vm3/image.qcow2 -U")
        # switch check if vm has to be booted
        self.mock_vms["vm3"].is_alive.assert_called_once_with()

    @mock.patch('avocado_i2n.state_setup.process')
    @mock.patch('avocado_i2n.state_setup.lv_utils')
    def test_set_multivm(self, mock_lv_utils, _mock_process):
        self._set_off_lvm_params()
        self.run_params["vms"] = "vm2 vm3 vm4"
        self.run_params["set_state"] = "launch2"
        self.run_params["set_state_vm2"] = "launch2"
        self.run_params["set_state_vm3"] = "root"
        self.run_params["set_state_vm4"] = "launch4"
        self.run_params["set_type"] = "off"
        self.run_params["set_mode_vm2"] = "rx"
        self.run_params["set_mode_vm3"] = "ff"
        self.run_params["set_mode_vm4"] = "aa"
        self.run_params["lv_size"] = "30G"
        self.run_params["pool_name"] = "thin_pool"
        self.run_params["pool_size"] = "30G"
        self.run_params["vg_name_vm2"] = "ramdisk_vm2"
        self.run_params["vg_name_vm3"] = "ramdisk_vm3"
        self.run_params["vg_name_vm4"] = "ramdisk_vm4"
        self.run_params["image_name_vm3"] = "/vm3/image"
        self.run_params["image_raw_device"] = "no"
        self.run_params["ramdisk_sparse_filename_vm3"] = "virtual_hdd_vm3"
        self.run_params["use_tmpfs"] = "yes"
        self.run_params["ramdisk_basedir"] = "/tmp"
        self.run_params["ramdisk_vg_size"] = "40000"
        self.run_params["skip_types"] = "on"
        self._create_mock_vms()
        self.exist_switch = False

        def lv_check_side_effect(_vgname, lvname):
            return True if lvname == "launch2" else False if lvname == "launch4" else False
        mock_lv_utils.lv_check.side_effect = lv_check_side_effect
        mock_lv_utils.vg_check.return_value = False

        with self.assertRaises(exceptions.TestSkipError):
            state_setup.set_state(self.run_params, self.env)

        expected = [mock.call("ramdisk_vm2", "launch2"), mock.call("ramdisk_vm3", "LogVol"), mock.call("ramdisk_vm4", "launch4")]
        self.assertListEqual(mock_lv_utils.lv_check.call_args_list, expected)
        self.mock_vms["vm2"].destroy.assert_called_once_with(gracefully=True)
        self.mock_vms["vm3"].destroy.assert_called_once_with(gracefully=True)
        self.mock_vms["vm4"].destroy.assert_called_once_with(gracefully=True)
        mock_lv_utils.vg_ramdisk.assert_called_once_with(None, 'ramdisk_vm3', '40000', '/tmp', 'virtual_hdd_vm3', True)
        mock_lv_utils.lv_create.assert_called_once_with('ramdisk_vm3', 'LogVol', '30G', pool_name='thin_pool', pool_size='30G')
        mock_lv_utils.lv_take_snapshot.assert_called_once_with('ramdisk_vm3', 'LogVol', 'current_state')

    @mock.patch('avocado_i2n.state_setup.process')
    @mock.patch('avocado_i2n.state_setup.lv_utils')
    def test_unset_multivm(self, mock_lv_utils, _mock_process):
        self.run_params["vms"] = "vm1 vm4"
        self.run_params["unset_state_vm1"] = "root"
        self.run_params["unset_state_vm4"] = "launch4"
        self.run_params["unset_type"] = "off"
        self.run_params["unset_mode_vm1"] = "ra"
        self.run_params["unset_mode_vm4"] = "fi"
        self.run_params["lv_name"] = "LogVol"
        self.run_params["vg_name_vm1"] = "ramdisk_vm1"
        self.run_params["vg_name_vm4"] = "ramdisk_vm4"
        self.run_params["image_name_vm1"] = "/vm1/image"
        self.run_params["image_raw_device"] = "no"
        self._create_mock_vms()
        self.exist_switch = False

        def lv_check_side_effect(_vgname, lvname):
            return True if lvname == "LogVol" else False
        mock_lv_utils.lv_check.side_effect = lv_check_side_effect

        state_setup.unset_state(self.run_params, self.env)

        expected = [mock.call("ramdisk_vm1", "LogVol"), mock.call("ramdisk_vm4", "launch4")]
        self.assertListEqual(mock_lv_utils.lv_check.call_args_list, expected)


if __name__ == '__main__':
    unittest.main()
