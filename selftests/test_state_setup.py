#!/usr/bin/env python

import unittest
import unittest.mock as mock
import os
import contextlib

from avocado import Test
from avocado.core import exceptions
from avocado.utils import process
from virttest import utils_params

import unittest_importer
# use old name to reduce amount of changes in the unit tests
from avocado_i2n.states import setup as ss
from avocado_i2n.states import qcow2
from avocado_i2n.states import lvm
from avocado_i2n.states import ramfile
from avocado_i2n.states import lxc
from avocado_i2n.states import btrfs
from avocado_i2n.states import pool
from avocado_i2n.states import vmnet


class MockDriver(unittest.TestCase):

    def __init__(self, params, mock_vms, mock_file_exists):
        super().__init__()

        self.state_backends = {"image": params["states_images"],
                               "vm": params["states_vms"],
                               "net": params["states_nets"]}

        self.mock_vms = mock_vms

        self.mock_file_exists = mock_file_exists
        self.mock_file_exists.side_effect = self._file_exists
        self.exist_switch = True
        self.exist_lambda = None

    def _only_lvm_root_exists(self, vg_name, lv_name):
        return True if lv_name == "LogVol" else False

    def _file_exists(self, filepath):
        # avocado's test class does some unexpected monkey patching
        if filepath.endswith(".expected"):
            return False
        if self.exist_lambda:
            return self.exist_lambda(filepath)
        return self.exist_switch

    def _reset_extra_mocks(self):
        for vmname in self.mock_vms:
            self.mock_vms[vmname].reset_mock()
        self.mock_file_exists.reset_mock()
        self.exist_switch = True
        self.exist_lambda = None

    @contextlib.contextmanager
    def mock_show(self, state_names, state_type):
        mock_driver = mock.MagicMock()
        backend = self.state_backends[state_type]
        if backend == "lvm":
            mock_driver.lv_list.return_value = state_names
            with mock.patch('avocado_i2n.states.lvm.lv_utils', mock_driver):
                yield mock_driver
        elif backend in ["qcow2", "qcow2vt"]:
            output = ""
            for state in state_names:
                size = "0 B" if state_type == "image" else "1 GiB"
                output += f"0         {state}         {size} 0000-00-00 00:00:00   00:00:00.000\n"
            mock_driver.return_value.snapshot_list.return_value = output
            with mock.patch('avocado_i2n.states.qcow2.QemuImg', mock_driver):
                yield mock_driver
        elif backend == "qcow2ext":
            mock_driver.listdir.return_value = [s + ".qcow2" for s in state_names]
        elif backend == "ramfile":
            ramfile.RamfileBackend.image_state_backend.show.return_value = state_names
            mock_driver.listdir.return_value = [s + ".state" for s in state_names]
            mock_driver.stat.return_value.st_size = 0
            mock_driver.path.join = os.path.join
            mock_driver.path.exists.return_value = True
            with mock.patch('avocado_i2n.states.ramfile.os', mock_driver):
                yield mock_driver
        else:
            raise ValueError(f"Unsupported backend for testing {backend}")

    @contextlib.contextmanager
    def mock_check(self, state_name, state_type, exists=True, root_exists=True):
        mock_driver = mock.MagicMock()
        self._reset_extra_mocks()
        backend = self.state_backends[state_type]
        # emulate only more regular pool backend behavior
        backend = "qcow2" if backend == "pool" else backend
        if backend == "lvm":
            mock_driver.lv_check.return_value = exists and root_exists
            mock_driver.lv_check.side_effect = self._only_lvm_root_exists if root_exists and not exists else None
            with mock.patch('avocado_i2n.states.lvm.lv_utils', mock_driver):
                yield mock_driver
        elif backend in ["qcow2", "qcow2vt"]:
            if backend == "qcow2":
                self.mock_vms["vm1"].is_alive.return_value = False
                self.exist_switch = root_exists
            elif backend == "qcow2vt":
                self.exist_switch = True
                self.mock_vms["vm1"].is_alive.return_value = root_exists
            size = "0 B" if state_type == "image" else "1 GiB"
            state = state_name
            output = f"0         {state}         {size} 0000-00-00 00:00:00   00:00:00.000\n"
            output = "" if not exists else output
            driver_instance = mock_driver.return_value
            driver_instance.snapshot_list.return_value = output
            with mock.patch('avocado_i2n.states.qcow2.QemuImg', mock_driver):
                yield mock_driver
        elif backend == "qcow2ext":
            mock_driver.listdir.return_value = [state_name + ".state"] if exists else []
            mock_driver.stat.return_value.st_size = 0
            mock_driver.path.join = os.path.join
            mock_driver.path.exists = self.mock_file_exists
            self.exist_switch = exists
            exist_lambda = lambda filename: filename.endswith("image.qcow2") or filename.endswith("/vm1")
            self.exist_lambda = exist_lambda if root_exists and not exists else None
            # TODO: this is in fact way too much mocking since it requires mocking various
            # other backends like shutil, QemuImg, etc. all of which are used - better convert
            # all these isolated boundary tests into proper integration tests to reduce mocking
            # the boundary of our dependencies and benefit from proper boundary change detection
            with mock.patch('avocado_i2n.states.qcow2.os', mock_driver):
                yield mock_driver
        elif backend == "ramfile":
            ramfile.RamfileBackend.image_state_backend.show.return_value = [state_name] if exists else []
            mock_driver.listdir.return_value = [state_name + ".state"] if exists else []
            mock_driver.stat.return_value.st_size = 0
            mock_driver.path.join = os.path.join
            mock_driver.path.isabs.return_value = False
            mock_driver.path.exists = self.mock_file_exists
            self.exist_switch = exists
            exist_lambda = lambda filename: filename.endswith("image.qcow2") or filename.endswith("/vm1")
            self.exist_lambda = exist_lambda if root_exists and not exists else None
            with mock.patch('avocado_i2n.states.ramfile.os', mock_driver):
                yield mock_driver
        else:
            raise ValueError(f"Unsupported backend for testing {backend}")

    def assert_show(self, mock_driver, _state_names, state_type):
        backend = self.state_backends[state_type]
        if backend == "lvm":
            mock_driver.lv_list.assert_called_once_with("disk_vm1")
        elif backend in ["qcow2", "qcow2vt"]:
            mock_driver.return_value.snapshot_list.assert_called_once_with(force_share=True)
        elif backend == "qcow2ext":
            mock_driver.listdir.assert_called_once_with("/images/vm1/image1")
        elif backend == "ramfile":
            mock_driver.listdir.assert_called_once_with("/images/vm1")
        else:
            raise ValueError(f"Unsupported backend for testing {backend}")

    def assert_check(self, mock_driver, state_name, state_type, action_type):
        # exists, root_exists s.t. 0 = 00, 1 = 01, 2 = 10, 3 = 11
        exists, root_exists = [bool(int(b)) for b in f'{action_type:02b}']
        backend = self.state_backends[state_type]
        if backend == "lvm":
            # assert root state is checked as a prerequisite
            expected_checks = [mock.call("disk_vm1", "LogVol"),
                               mock.call("disk_vm1", state_name)]
            # assert actual state is checked when root is available
            expected_checks = expected_checks[:1] if not root_exists else expected_checks
            self.assertListEqual(mock_driver.lv_check.call_args_list, expected_checks)
            if not root_exists:
                self.mock_vms["vm1"].destroy.assert_not_called()
        elif backend in ["qcow2", "qcow2vt"]:
            # assert root state is checked as a prerequisite
            self.mock_file_exists.assert_called_once_with("/images/vm1/image.qcow2")
            self.mock_vms["vm1"].is_alive.assert_called()
            # assert actual state is checked only when root is available
            if root_exists:
                mock_driver.return_value.snapshot_list.assert_called_once_with(force_share=True)
            else:
                mock_driver.return_value.snapshot_list.assert_not_called()
        elif backend == "qcow2ext":
            # assert root state is checked as a prerequisite
            expected_checks = [mock.call(f"/images/vm1"),
                               mock.call(f"/images/vm1/image.qcow2")]
            if not root_exists:
                expected_checks = expected_checks[:1]
            self.assertListEqual(mock_driver.path.exists.call_args_list, expected_checks)
            # assert actual state is checked when root is available
            if root_exists:
                # TODO: cannot assert state_name as we need more isolated testing here
                mock_driver.listdir.assert_called_once_with(f"/images/vm1/image1")
        elif backend == "ramfile":
            # assert root state is checked as a prerequisite
            expected_checks = [mock.call(f"/images/vm1"),
                               mock.call(f"/images/vm1/image.qcow2")]
            if not root_exists:
                expected_checks = expected_checks[:1]
            else:
                self.mock_vms["vm1"].is_alive.assert_called()
            self.assertListEqual(mock_driver.path.exists.call_args_list, expected_checks)
            # assert actual state is checked when root is available
            if root_exists:
                # TODO: cannot assert state_name as we need more isolated testing here
                mock_driver.listdir.assert_called_once_with(f"/images/vm1")
        else:
            raise ValueError(f"Unsupported backend for testing {backend}")

    def assert_get(self, mock_driver, state_name, state_type, action_type):
        backend = self.state_backends[state_type]
        if backend == "lvm":
            mock_driver.lv_check.assert_called_with("disk_vm1", "launch")
            if action_type == 1:
                mock_driver.lv_remove.assert_called_once_with('disk_vm1', 'current_state')
                mock_driver.lv_take_snapshot.assert_called_once_with('disk_vm1', state_name, 'current_state')
            else:
                mock_driver.lv_remove.assert_not_called()
                mock_driver.lv_take_snapshot.assert_not_called()
        elif backend in ["qcow2", "qcow2vt"]:
            driver_instance = mock_driver.return_value
            driver_instance.snapshot_list.assert_called_once_with(force_share=True)
            if action_type == 1 and state_type == "image":
                mock_driver.assert_called()
                second_creation_call_params = mock_driver.call_args_list[-2].args[0]
                self.assertEqual(second_creation_call_params["get_state"], state_name)
                driver_instance.snapshot_apply.assert_called_once_with()
            elif action_type == 1 and state_type == "vm":
                self.mock_vms["vm1"].loadvm.assert_called_once_with(state_name)
            elif state_type == "image":
                driver_instance.assert_not_called()
            else:
                self.mock_vms["vm1"].loadvm.assert_not_called()
        elif backend == "qcow2ext":
            # would have to mock a different dependency (QemuImg) here
            raise NotImplementedError("Not isolated backend - test via integration")
        elif backend == "ramfile":
            # TODO: cannot assert state_name as we need more isolated testing here
            mock_driver.listdir.assert_called_once_with(f"/images/vm1")
            if action_type == 1:
                self.mock_vms["vm1"].restore_from_file.assert_called_once_with(f"/images/vm1/{state_name}.state")
            else:
                self.mock_vms["vm1"].restore_from_file.assert_not_called()
        else:
            raise ValueError(f"Unsupported backend for testing {backend}")

    def assert_set(self, mock_driver, state_name, state_type, action_type):
        backend = self.state_backends[state_type]
        if backend == "lvm":
            if action_type == 1:
                mock_driver.lv_check.assert_any_call("disk_vm1", state_name)
                mock_driver.lv_remove.assert_not_called()
                mock_driver.lv_take_snapshot.assert_called_once_with('disk_vm1', 'current_state', state_name)
            elif action_type == 2:
                mock_driver.lv_check.assert_called_with("disk_vm1", state_name)
                mock_driver.lv_remove.assert_called_once_with('disk_vm1', state_name)
                mock_driver.lv_take_snapshot.assert_called_once_with('disk_vm1', 'current_state', state_name)
            else:
                mock_driver.lv_remove.assert_not_called()
                mock_driver.lv_take_snapshot.assert_not_called()
        elif backend in ["qcow2", "qcow2vt"]:
            mock_driver.return_value.snapshot_list.assert_called_once_with(force_share=True)
            if action_type == 1 and state_type == "image":
                mock_driver.assert_called()
                second_creation_call_params = mock_driver.call_args_list[-2].args[0]
                self.assertEqual(second_creation_call_params["set_state"], state_name)
                mock_driver.return_value.snapshot_create.assert_called_once_with()
            elif action_type == 2 and state_type == "image":
                mock_driver.assert_called()
                second_creation_call_params = mock_driver.call_args_list[-2].args[0]
                self.assertEqual(second_creation_call_params["set_state"], state_name)
                mock_driver.return_value.snapshot_del.assert_called_once_with()
                mock_driver.return_value.snapshot_create.assert_called_once_with()
            elif state_type == "image":
                mock_driver.return_value.assert_not_called()
            elif action_type in [1, 2] and state_type == "vm":
                self.mock_vms["vm1"].savevm.assert_called_once_with(state_name)
            else:
                self.mock_vms["vm1"].savevm.assert_not_called()
        elif backend == "qcow2ext":
            # would have to mock a different dependency (shutil.copy) here
            raise NotImplementedError("Not isolated backend - test via integration")
        elif backend == "ramfile":
            if action_type in [1, 2]:
                # TODO: cannot assert state_name as we need more isolated testing here
                mock_driver.listdir.assert_called_once_with(f"/images/vm1")
                self.mock_vms["vm1"].save_to_file.assert_called_once_with(f"/images/vm1/{state_name}.state")
            else:
                self.mock_vms["vm1"].save_to_file.assert_not_called()
        else:
            raise ValueError(f"Unsupported backend for testing {backend}")

    def assert_unset(self, mock_driver, state_name, state_type, action_type):
        backend = self.state_backends[state_type]
        if backend == "lvm":
            mock_driver.lv_check.assert_called_with("disk_vm1", state_name)
            if action_type == 1:
                mock_driver.lv_remove.assert_called_once_with("disk_vm1", state_name)
                mock_driver.lv_take_snapshot.assert_not_called()
            else:
                mock_driver.lv_remove.assert_not_called()
                mock_driver.lv_take_snapshot.assert_not_called()
        elif backend in ["qcow2", "qcow2vt"]:
            mock_driver.return_value.snapshot_list.assert_called_once_with(force_share=True)
            if action_type == 1 and state_type == "image":
                mock_driver.assert_called()
                second_creation_call_params = mock_driver.call_args_list[-2].args[0]
                self.assertEqual(second_creation_call_params["unset_state"], state_name)
                mock_driver.return_value.snapshot_del.assert_called_once_with()
            elif action_type == 1 and state_type == "vm":
                self.mock_vms["vm1"].monitor.send_args_cmd.assert_called_once_with(f'delvm id={state_name}')
            elif state_type == "image":
                mock_driver.return_value.assert_not_called()
            else:
                self.mock_vms["vm1"].monitor.send_args_cmd.assert_not_called()
        elif backend == "qcow2ext":
            # TODO: cannot assert state_name as we need more isolated testing here
            mock_driver.listdir.assert_called_once_with(f"/images/vm1/image1")
            if action_type == 1:
                mock_driver.unlink.assert_called_once_with(f"/images/vm1/{state_name}.qcow2")
            else:
                mock_driver.unlink.assert_not_called()
        elif backend == "ramfile":
            # TODO: cannot assert state_name as we need more isolated testing here
            mock_driver.listdir.assert_called_once_with(f"/images/vm1")
            if action_type == 1:
                mock_driver.unlink.assert_called_once_with(f"/images/vm1/{state_name}.state")
            else:
                mock_driver.unlink.assert_not_called()
        else:
            raise ValueError(f"Unsupported backend for testing {backend}")


@mock.patch('avocado_i2n.states.lvm.os.mkdir', mock.Mock(return_value=0))
@mock.patch('avocado_i2n.states.lvm.os.makedirs', mock.Mock(return_value=0))
@mock.patch('avocado_i2n.states.lvm.os.rmdir', mock.Mock(return_value=0))
@mock.patch('avocado_i2n.states.lvm.os.unlink', mock.Mock(return_value=0))
@mock.patch('avocado_i2n.states.lvm.shutil.rmtree', mock.Mock(return_value=0))
@mock.patch('avocado_i2n.states.pool.os.makedirs', mock.Mock(return_value=0))
class StateSetupTest(Test):

    def setUp(self):
        self.run_str = ""

        ss.BACKENDS = {"lvm": lvm.LVMBackend, "qcow2": qcow2.QCOW2Backend,
                       "lxc": lxc.LXCBackend, "btrfs": btrfs.BtrfsBackend,
                       "pool": pool.QCOW2PoolBackend, "qcow2vt": qcow2.QCOW2VTBackend,
                       "ramfile": ramfile.RamfileBackend, "vmnet": vmnet.VMNetBackend}
        ramfile.RamfileBackend.image_state_backend = mock.MagicMock()

        # disable pool locks for easier mocking
        pool.SKIP_LOCKS = True

        self.run_params = utils_params.Params()
        self.run_params["nets"] = "net1"
        self.run_params["vms"] = "vm1"
        self.run_params["images"] = "image1"
        self.run_params["main_vm"] = "vm1"
        self.run_params["image_name_vm1"] = "vm1/image"
        self.run_params["images_base_dir"] = "/images"
        self.run_params["nets"] = "net1"
        self.run_params["states_chain"] = "nets vms images"
        self.run_params["states_nets"] = "vmnet"
        self.run_params["states_images"] = "pool"
        self.run_params["states_vms"] = "qcow2vt"
        self.run_params["check_mode"] = "rr"

        self.env = mock.MagicMock(name='env')
        self.env.get_vm = mock.MagicMock(side_effect=self._get_mock_vm)

        self.mock_vms = {}
        self.driver = None

        self.mock_file_exists = mock.MagicMock()
        exists_patch = mock.patch('avocado_i2n.states.setup.os.path.exists',
                                  self.mock_file_exists)
        exists_patch.start()
        self.addCleanup(exists_patch.stop)

    def _set_image_lvm_params(self):
        self.run_params["states_images"] = "lvm"
        self.run_params["vg_name_vm1"] = "disk_vm1"
        self.run_params["lv_name"] = "LogVol"
        self.run_params["lv_pointer_name"] = "current_state"

    def _set_image_qcow2_params(self):
        self.run_params["states_images"] = "qcow2"
        self.run_params["image_format"] = "qcow2"
        self.run_params["qemu_img_binary"] = "qemu-img"

    def _set_image_pool_params(self):
        self.run_params["states_images"] = "pool"
        self.run_params["image_pool"] = "/data/pool"
        self.run_params["image_format"] = "qcow2"
        self.run_params["qemu_img_binary"] = "qemu-img"

    def _set_vm_qcow2_params(self):
        self.run_params["states_vms"] = "qcow2vt"
        self.run_params["image_format"] = "qcow2"
        self.run_params["qemu_img_binary"] = "qemu-img"

    def _set_vm_ramfile_params(self):
        self.run_params["states_vms"] = "ramfile"

    def _get_mock_vm(self, vm_name):
        return self.mock_vms[vm_name]

    def _create_mock_vms(self):
        for vm_name in self.run_params.objects("vms"):
            self.mock_vms[vm_name] = mock.MagicMock(name=vm_name)
            self.mock_vms[vm_name].name = vm_name
            self.mock_vms[vm_name].params = self.run_params.object_params(vm_name)

    def _create_mock_driver(self):
        self.driver = MockDriver(self.run_params,
                                 self.mock_vms, self.mock_file_exists)

    def _prepare_driver_from_backend(self, backend):
        self._create_mock_vms()

        if backend in ["qcow2", "qcow2ext", "lvm", "pool"]:
            backend_type = "image"
            self.run_params["skip_types"] = "nets nets/vms"
        else:
            backend_type = "vm"
            self.run_params["skip_types"] = "nets nets/vms/images"
        if backend == "pool":
            self._set_image_pool_params()
        elif backend == "qcow2":
            self._set_image_qcow2_params()
        elif backend == "qcow2ext":
            self._set_image_qcow2_params()
        elif backend == "lvm":
            self._set_image_lvm_params()
        elif backend == "qcow2vt":
            self._set_vm_qcow2_params()
        elif backend == "ramfile":
            self._set_vm_ramfile_params()
        self._create_mock_driver()

        return backend_type

    def _test_show_states(self, backend):
        backend_type = self._prepare_driver_from_backend(backend)

        # assert empty list without available states
        with self.driver.mock_show([], backend_type) as driver:
            states = ss.show_states(self.run_params, self.env)
            self.driver.assert_show(driver, [], backend_type)
        self.assertEqual(len(states), 0)

        # assert nonempty list with available states
        with self.driver.mock_show(["launch", "launch_2-0", "launch3.0"], backend_type) as driver:
            states = ss.show_states(self.run_params, self.env)
            self.driver.assert_show(driver, ["launch", "launch_2-0", "launch3.0"], backend_type)
        self.assertEqual(len(states), 3)
        self.assertIn("launch", states)
        self.assertIn("launch_2-0", states)
        self.assertIn("launch3.0", states)
        self.assertNotIn("root", states)
        self.assertNotIn("boot", states)

    def _test_check_state(self, backend):
        backend_type = self._prepare_driver_from_backend(backend)
        self.run_params[f"check_state_{backend_type}s_vm1"] = "launch"

        # assert behavior on root and state availability
        with self.driver.mock_check("launch", backend_type, True, True) as driver:
            exists = ss.check_states(self.run_params, self.env)
            self.driver.assert_check(driver, "launch", backend_type, 3)
        self.assertTrue(exists)

        # assert behavior on root availability
        with self.driver.mock_check("launch", backend_type, False, True) as driver:
            exists = ss.check_states(self.run_params, self.env)
            self.driver.assert_check(driver, "launch", backend_type, 1)
        self.assertFalse(exists)

        # assert behavior on no root availability
        with self.driver.mock_check("launch", backend_type, False, False) as driver:
            exists = ss.check_states(self.run_params, self.env)
            self.driver.assert_check(driver, "launch", backend_type, 0)
        self.assertFalse(exists)

    def _test_get_state(self, backend):
        backend_type = self._prepare_driver_from_backend(backend)
        self.run_params[f"get_state_{backend_type}s_vm1"] = "launch"

        # assert state is retrieved if available after it was checked
        with self.driver.mock_check("launch", backend_type, True) as driver:
            ss.get_states(self.run_params, self.env)
            self.driver.assert_get(driver, "launch", backend_type, 1)

        # assert state is not retrieved if not available after it was checked
        with self.driver.mock_check("launch", backend_type, False) as driver:
            ss.get_states(self.run_params, self.env)
            self.driver.assert_get(driver, "launch", backend_type, 0)

    def _test_set_state(self, backend):
        backend_type = self._prepare_driver_from_backend(backend)
        self.run_params[f"set_state_{backend_type}s_vm1"] = "launch"

        # assert state is removed and saved if available after it was checked
        with self.driver.mock_check("launch", backend_type, True) as driver:
            ss.set_states(self.run_params, self.env)
            self.driver.assert_set(driver, "launch", backend_type, 2)

        # assert state is saved if not available after it was checked
        with self.driver.mock_check("launch", backend_type, False) as driver:
            ss.set_states(self.run_params, self.env)
            self.driver.assert_set(driver, "launch", backend_type, 1)

    def _test_unset_state(self, backend):
        backend_type = self._prepare_driver_from_backend(backend)
        self.run_params[f"unset_state_{backend_type}s_vm1"] = "launch"

        # assert state is removed if available after it was checked
        with self.driver.mock_check("launch", backend_type, True) as driver:
            ss.unset_states(self.run_params, self.env)
            self.driver.assert_unset(driver, "launch", backend_type, 1)

        # assert state is not removed if not available after it was checked
        with self.driver.mock_check("launch", backend_type, False) as driver:
            ss.unset_states(self.run_params, self.env)
            self.driver.assert_unset(driver, "launch", backend_type, 0)

    def test_show_image_lvm(self):
        """Test that state listing with the LVM backend works correctly."""
        self._test_show_states("lvm")

    def test_show_image_qcow2(self):
        """Test that state listing with the QCOW2 internal state backend works correctly."""
        self._test_show_states("qcow2")

    def test_show_image_qcow2ext(self):
        """Test that state listing with the QCOW2 external state backend works correctly."""
        self._test_show_states("qcow2ext")

    def test_show_vm_qcow2(self):
        """Test that state listing with the QCOW2VT backend works correctly."""
        self._test_show_states("qcow2vt")

    def test_show_vm_ramfile(self):
        """Test that state listing with the ramfile backend works correctly."""
        self._test_show_states("ramfile")

    def test_check_image_lvm(self):
        """Test that state checking with the LVM backend works correctly."""
        self._test_check_state("lvm")

    def test_check_image_qcow2(self):
        """Test that state checking with the QCOW2 internal state backend works correctly."""
        self._test_check_state("qcow2")

    def test_check_image_qcow2ext(self):
        """Test that state checking with the QCOW2 external state backend works correctly."""
        self._test_check_state("qcow2ext")

    def test_check_image_qcow2_boot(self):
        """
        Test that state checking with the QCOW2 backend considers running vms.

        .. todo:: Consider whether this is a good approach to spread to other
            state backends or rid ourselves of the QCOW2(VT) hacks altogether.
        """
        backend = "qcow2"
        backend_type = self._prepare_driver_from_backend(backend)
        self.run_params[f"check_state_{backend_type}s_vm1"] = "launch"

        # assert behavior on root and state availability
        with self.driver.mock_check("launch", backend_type, True, True) as driver:
            self.mock_vms["vm1"].is_alive.return_value = True
            exists = ss.check_states(self.run_params, self.env)
            # TODO: define more action types to achieve backend independence here,
            # perhaps after we generalize run vm requirement to all backend roots
            #self.driver.assert_check(driver, "launch", backend_type, 2)
            # assert root state is checked as a prerequisite
            # assert off switch as part of root state is checked as a prerequisite
            self.mock_vms["vm1"].is_alive.assert_called()
            self.mock_file_exists.assert_not_called()
            # assert actual state is not checked and not available
            driver.system_output.assert_not_called()
        self.assertFalse(exists)

    def test_check_vm_qcow2(self):
        """Test that state checking with the QCOW2VT backend works correctly."""
        self._test_check_state("qcow2")

    def test_check_vm_qcow2_noimage(self):
        """
        Test that state checking with the QCOW2VT backend considers missing images.

        .. todo:: Consider whether this is a good approach to spread to other
            state backends or rid ourselves of the QCOW2(VT) hacks altogether.
        """
        backend = "qcow2vt"
        backend_type = self._prepare_driver_from_backend(backend)
        self.run_params[f"check_state_{backend_type}s_vm1"] = "launch"

        with self.driver.mock_check("launch", backend_type, True, True) as driver:
            self.driver.exist_switch = False
            exists = ss.check_states(self.run_params, self.env)
            # TODO: define more action types to achieve backend independence here,
            # perhaps after we generalize run vm requirement to all backend roots
            #self.driver.assert_check(driver, "launch", backend_type, 2)
            # assert root state is checked as a prerequisite
            # assert missing image as part of root state is checked as a prerequisite
            self.mock_file_exists.assert_called_once_with("/images/vm1/image.qcow2")
            self.mock_vms["vm1"].is_alive.assert_not_called()
            # assert actual state is not checked and not available
            driver.system_output.assert_not_called()
        self.assertFalse(exists)

    def test_check_vm_ramfile(self):
        """Test that state checking with the ramfile backend works correctly."""
        self._test_check_state("ramfile")

    def test_check_forced_root(self):
        """Test that state checking with a state backend can provide roots."""
        backend = "qcow2vt"
        backend_type = self._prepare_driver_from_backend(backend)
        self.run_params[f"check_state_{backend_type}s_vm1"] = "launch"
        self.run_params["check_mode"] = "ff"

        # assert root state is not detected then created to check the actual state
        with self.driver.mock_check("launch", backend_type, False, False) as driver:
            exists = ss.check_states(self.run_params, self.env)
            # TODO: define more action types to achieve backend independence here,
            # perhaps after we generalize run vm requirement to all backend roots
            #self.driver.assert_check(driver, "launch", backend_type, 2)
            # assert root state is checked as a prerequisite
            self.mock_vms["vm1"].is_alive.assert_called()
            # assert root state is provided from the check
            self.mock_vms["vm1"].create.assert_called_once()
            # assert actual state is still checked and not available
            driver.return_value.snapshot_list.assert_called_once_with(force_share=True)
        self.assertFalse(exists)

    def test_get_image_lvm(self):
        """Test that state getting with the LVM backend works with available root."""
        # use a nondefault policy that doesn't raise any errors here
        self.run_params["get_mode_vm1"] = "ri"
        self._test_get_state("lvm")

    def test_get_image_qcow2(self):
        """Test that state getting with the QCOW2 backend works with available root."""
        # use a nondefault policy that doesn't raise any errors here
        self.run_params["get_mode_vm1"] = "ri"
        self._test_get_state("qcow2")

    def test_get_aa(self):
        """Test that state getting works with abort policies."""
        backend = "lvm"
        backend_type = self._prepare_driver_from_backend(backend)
        self.run_params[f"get_state_{backend_type}s_vm1"] = "launch"
        self.run_params["get_mode_vm1"] = "aa"

        # assert state retrieval is aborted if state is available
        with self.driver.mock_check("launch", backend_type, True) as driver:
            with self.assertRaises(exceptions.TestAbortError):
                ss.get_states(self.run_params, self.env)
            self.driver.assert_get(driver, "launch", backend_type, False)

        # assert state retrieval is aborted if state is not available
        with self.driver.mock_check("launch", backend_type, False) as driver:
            with self.assertRaises(exceptions.TestAbortError):
                ss.get_states(self.run_params, self.env)
            self.driver.assert_get(driver, "launch", backend_type, False)

    def test_get_rx(self):
        """Test that state getting works with reuse policy."""
        backend = "lvm"
        backend_type = self._prepare_driver_from_backend(backend)
        self.run_params[f"get_state_{backend_type}s_vm1"] = "launch"
        self.run_params["get_mode_vm1"] = "rx"

        # assert state retrieval is reused if available
        with self.driver.mock_check("launch", backend_type, True) as driver:
            ss.get_states(self.run_params, self.env)
            self.driver.assert_get(driver, "launch", backend_type, True)

    def test_get_ii(self):
        """Test that state getting works with ignore policies."""
        backend = "lvm"
        backend_type = self._prepare_driver_from_backend(backend)
        self.run_params[f"get_state_{backend_type}s_vm1"] = "launch"
        self.run_params["get_mode_vm1"] = "ii"

        # assert state retrieval is ignored if state is available
        with self.driver.mock_check("launch", backend_type, True) as driver:
            ss.get_states(self.run_params, self.env)
            self.driver.assert_get(driver, "launch", backend_type, False)

        # assert state retrieval is ignored if state is not available
        with self.driver.mock_check("launch", backend_type, False) as driver:
            ss.get_states(self.run_params, self.env)
            self.driver.assert_get(driver, "launch", backend_type, False)

    def test_get_xx(self):
        """Test that state getting detects invalid policies."""
        backend = "lvm"
        backend_type = self._prepare_driver_from_backend(backend)
        self.run_params[f"get_state_{backend_type}s_vm1"] = "launch"
        self.run_params["get_mode_vm1"] = "xx"

        # assert invalid policy x if state is available
        with self.driver.mock_check("launch", backend_type, True) as driver:
            with self.assertRaises(exceptions.TestError):
                ss.get_states(self.run_params, self.env)
            self.driver.assert_get(driver, "launch", backend_type, False)

        # assert invalid policy x if state is not available
        with self.driver.mock_check("launch", backend_type, False) as driver:
            with self.assertRaises(exceptions.TestError):
                ss.get_states(self.run_params, self.env)
            self.driver.assert_get(driver, "launch", backend_type, False)

    def test_get_vm_qcow2(self):
        """Test that state getting with the QCOW2VT backend works with available root."""
        # use a nondefault policy that doesn't raise any errors here
        self.run_params["get_mode_vm1"] = "ri"
        self._test_get_state("qcow2vt")

    def test_get_vm_ramfile(self):
        """Test that state getting with the ramdisk backend works with available root."""
        # use a nondefault policy that doesn't raise any errors here
        self.run_params["get_mode_vm1"] = "ri"
        self._test_get_state("ramfile")

    def test_set_image_lvm(self):
        """Test that state setting with the LVM backend works with available root."""
        self._test_set_state("lvm")

    def test_set_image_qcow2(self):
        """Test that state setting with the QCOW2 backend works with available root."""
        self._test_set_state("qcow2")

    def test_set_aa(self):
        """Test that state setting works with abort policies."""
        backend = "lvm"
        backend_type = self._prepare_driver_from_backend(backend)
        self.run_params[f"set_state_{backend_type}s_vm1"] = "launch"
        self.run_params["set_mode_vm1"] = "aa"

        # assert state saving is aborted if state is available
        with self.driver.mock_check("launch", backend_type, True) as driver:
            with self.assertRaises(exceptions.TestAbortError):
                ss.set_states(self.run_params, self.env)
            self.driver.assert_set(driver, "launch", backend_type, 0)

        # assert state saving is aborted if state is not available
        with self.driver.mock_check("launch", backend_type, False) as driver:
            with self.assertRaises(exceptions.TestAbortError):
                ss.set_states(self.run_params, self.env)
            self.driver.assert_set(driver, "launch", backend_type, 0)

    def test_set_rx(self):
        """Test that state setting works with reuse policy."""
        backend = "lvm"
        backend_type = self._prepare_driver_from_backend(backend)
        self.run_params[f"set_state_{backend_type}s_vm1"] = "launch"
        self.run_params["set_mode_vm1"] = "rx"

        # assert state saving is skipped if reusable state is available
        with self.driver.mock_check("launch", backend_type, True) as driver:
            ss.set_states(self.run_params, self.env)
            self.driver.assert_set(driver, "launch", backend_type, 0)

    def test_set_ff(self):
        """Test that state setting works with force policies."""
        backend = "lvm"
        backend_type = self._prepare_driver_from_backend(backend)
        self.run_params[f"set_state_{backend_type}s_vm1"] = "launch"
        self.run_params["set_mode_vm1"] = "ff"

        # assert state saving is forced if state is available
        with self.driver.mock_check("launch", backend_type, True) as driver:
            ss.set_states(self.run_params, self.env)
            self.driver.assert_set(driver, "launch", backend_type, 2)

        # assert state saving is forced if state is not available
        with self.driver.mock_check("launch", backend_type, False) as driver:
            ss.set_states(self.run_params, self.env)
            self.driver.assert_set(driver, "launch", backend_type, 1)

        # assert state saving cannot be forced if state root is not available
        with self.driver.mock_check("launch", backend_type, False) as driver:
            driver.lv_check.side_effect = None
            driver.lv_check.return_value = False
            with self.assertRaises(exceptions.TestError):
                ss.set_states(self.run_params, self.env)
            self.driver.assert_set(driver, "launch", backend_type, 0)

    def test_set_xx(self):
        """Test that state setting detects invalid policies."""
        backend = "lvm"
        backend_type = self._prepare_driver_from_backend(backend)
        self.run_params[f"set_state_{backend_type}s_vm1"] = "launch"
        self.run_params["set_mode_vm1"] = "xx"

        # assert invalid policy x if state is available
        with self.driver.mock_check("launch", backend_type, True) as driver:
            with self.assertRaises(exceptions.TestError):
                ss.set_states(self.run_params, self.env)
            self.driver.assert_set(driver, "launch", backend_type, 0)

        # assert invalid policy x if state is not available
        with self.driver.mock_check("launch", backend_type, False) as driver:
            with self.assertRaises(exceptions.TestError):
                ss.set_states(self.run_params, self.env)
            self.driver.assert_set(driver, "launch", backend_type, 0)

    def test_set_vm_qcow2(self):
        """Test that state setting with the QCOW2VT backend works with available root."""
        self._test_set_state("qcow2vt")

    def test_set_vm_ramfile(self):
        """Test that state setting with the ramfile backend works with available root."""
        self._test_set_state("ramfile")

    def test_unset_image_lvm(self):
        """Test that state unsetting with the LVM backend works with available root."""
        self._test_unset_state("lvm")

    def test_unset_image_qcow2(self):
        """Test that state unsetting with the QCOW2 internal state backend works with available root."""
        self._test_unset_state("qcow2")

    def test_unset_image_qcow2ext(self):
        """Test that state unsetting with the QCOW2 external state backend works with available root."""
        self._test_unset_state("qcow2ext")

    def test_unset_ra(self):
        """Test that state unsetting works with reuse and abort policy."""
        backend = "lvm"
        backend_type = self._prepare_driver_from_backend(backend)
        self.run_params[f"unset_state_{backend_type}s_vm1"] = "launch"
        self.run_params["unset_mode_vm1"] = "ra"

        # assert state removal is skipped if reusable state is available
        with self.driver.mock_check("launch", backend_type, True) as driver:
            ss.unset_states(self.run_params, self.env)
            self.driver.assert_unset(driver, "launch", backend_type, 0)

        # assert state removal is aborted if state is not available
        with self.driver.mock_check("launch", backend_type, False) as driver:
            with self.assertRaises(exceptions.TestAbortError):
                ss.unset_states(self.run_params, self.env)
            self.driver.assert_unset(driver, "launch", backend_type, 0)

    def test_unset_fi(self):
        """Test that state unsetting works with force and ignore policy."""
        backend = "lvm"
        backend_type = self._prepare_driver_from_backend(backend)
        self.run_params[f"unset_state_{backend_type}s_vm1"] = "launch"
        self.run_params["unset_mode_vm1"] = "fi"

        # assert state removal is forced if state is available
        with self.driver.mock_check("launch", backend_type, True) as driver:
            ss.unset_states(self.run_params, self.env)
            self.driver.assert_unset(driver, "launch", backend_type, 1)

        # assert state removal is ignored if state is not available
        with self.driver.mock_check("launch", backend_type, False) as driver:
            ss.unset_states(self.run_params, self.env)
            self.driver.assert_unset(driver, "launch", backend_type, 0)

    def test_unset_xx(self):
        """Test that state unsetting detects invalid policies."""
        backend = "lvm"
        backend_type = self._prepare_driver_from_backend(backend)
        self.run_params[f"unset_state_{backend_type}s_vm1"] = "launch"
        self.run_params["unset_mode_vm1"] = "xx"

        # assert invalid policy x if state is available
        with self.driver.mock_check("launch", backend_type, True) as driver:
            with self.assertRaises(exceptions.TestError):
                ss.unset_states(self.run_params, self.env)
            self.driver.assert_unset(driver, "launch", backend_type, 0)

        # assert invalid policy x if state is not available
        with self.driver.mock_check("launch", backend_type, False) as driver:
            with self.assertRaises(exceptions.TestError):
                ss.unset_states(self.run_params, self.env)
            self.driver.assert_unset(driver, "launch", backend_type, 0)

    def test_unset_vm_qcow2(self):
        """Test that state unsetting with the QCOW2VT backend works with available root."""
        self._test_unset_state("qcow2vt")

    def test_unset_vm_ramfile(self):
        """Test that state unsetting with the ramfile backend works with available root."""
        self._test_unset_state("ramfile")

    def test_unset_image_lvm_keep_pointer(self):
        """Test that LVM backend's pointer state cannot be unset."""
        backend = "lvm"
        backend_type = self._prepare_driver_from_backend(backend)
        self.run_params["unset_state_images_vm1"] = "current_state"

        with self.driver.mock_check("launch", backend_type, True) as driver:
            with self.assertRaises(ValueError):
                ss.unset_states(self.run_params, self.env)
            self.driver.assert_unset(driver, "current_state", backend_type, 0)

    def test_check_root_image_lvm(self):
        """Test that root checking with the LVM backend works."""
        backend = "lvm"
        backend_type = self._prepare_driver_from_backend(backend)
        self.run_params[f"check_state_{backend_type}s_vm1"] = "root"

        # assert root state is correctly detected
        with self.driver.mock_check("launch", backend_type, False, True) as driver:
            exists = ss.check_states(self.run_params, self.env)
            driver.lv_check.assert_called_once_with("disk_vm1", "LogVol")
        self.assertTrue(exists)

        # assert root state is correctly not detected
        with self.driver.mock_check("launch", backend_type, False, False) as driver:
            exists = ss.check_states(self.run_params, self.env)
            driver.lv_check.assert_called_once_with("disk_vm1", "LogVol")
        self.assertFalse(exists)

    def test_check_root_image_qcow2(self):
        """Test that root checking with the QCOW2 backend works."""
        backend = "qcow2"
        backend_type = self._prepare_driver_from_backend(backend)
        self.run_params[f"check_state_{backend_type}s_vm1"] = "root"
        # bonus: test for two images rather than one
        self.run_params["images_vm1"] = "image1 image2"
        self.run_params["image_name_image1_vm1"] = "vm1/image1"
        self.run_params["image_name_image2_vm1"] = "vm1/image2"

        # assert root state is correctly detected
        with self.driver.mock_check("launch", backend_type, False, True) as driver:
            exists = ss.check_states(self.run_params, self.env)
        self.assertTrue(exists)

        # assert root state is correctly not detected
        with self.driver.mock_check("launch", backend_type, False, False) as driver:
            exists = ss.check_states(self.run_params, self.env)
        self.assertFalse(exists)

        # assert running vms result in not completely available root state
        with self.driver.mock_check("launch", backend_type, False, True) as driver:
            self.mock_vms["vm1"].is_alive.return_value = True
            exists = ss.check_states(self.run_params, self.env)
        self.assertFalse(exists)

    @mock.patch('avocado_i2n.states.pool.shutil')
    def test_check_root_image_pool(self, mock_shutil):
        """Test that root checking with the pool backend works."""
        # bonus: test for two images rather than one
        self.run_params["images_vm1"] = "image1 image2"
        self.run_params["image_name_image1_vm1"] = "vm1/image1"
        self.run_params["image_name_image2_vm1"] = "vm1/image2"
        backend = "pool"
        backend_type = self._prepare_driver_from_backend(backend)
        self.run_params[f"check_state_{backend_type}s_vm1"] = "root"

        # consider local root with priority
        mock_shutil.reset_mock()
        with self.driver.mock_check("launch", backend_type, False, True) as driver:
            exists = ss.check_states(self.run_params, self.env)
        mock_shutil.copy.assert_not_called()
        self.assertTrue(exists)

        # consider pool root as well
        mock_shutil.reset_mock()
        with self.driver.mock_check("launch", backend_type, False, False) as driver:
            exists = ss.check_states(self.run_params, self.env)
        mock_shutil.copy.assert_not_called()
        self.assertFalse(exists)

        # the root state exists (and is downloaded) if its pool counterpart exists
        mock_shutil.reset_mock()
        with self.driver.mock_check("launch", backend_type, False, False) as driver:
            self.driver.exist_lambda = lambda filename: filename.startswith("/data/pool")
            exists = ss.check_states(self.run_params, self.env)
        expected_checks = [mock.call("/data/pool/vm1/image1.qcow2", "/images/vm1/image1.qcow2"),
                           mock.call("/data/pool/vm1/image2.qcow2", "/images/vm1/image2.qcow2")]
        self.assertListEqual(mock_shutil.copy.call_args_list, expected_checks)
        self.assertTrue(exists)

    def test_check_root_vm_qcow2(self):
        """Test that root checking with the QCOW2VT backend works."""
        backend = "qcow2vt"
        backend_type = self._prepare_driver_from_backend(backend)
        self.run_params[f"check_state_{backend_type}s_vm1"] = "root"

        # assert root state is correctly detected
        with self.driver.mock_check("launch", backend_type, False, True) as driver:
            exists = ss.check_states(self.run_params, self.env)
            self.mock_vms["vm1"].is_alive.assert_called_once_with()
        self.assertTrue(exists)

        # assert root state is correctly not detected
        with self.driver.mock_check("launch", backend_type, False, False) as driver:
            exists = ss.check_states(self.run_params, self.env)
            self.mock_vms["vm1"].is_alive.assert_called_once_with()
        self.assertFalse(exists)

    def test_check_root_vm_ramfile(self):
        """Test that root checking with the ramdisk backend works."""
        backend = "ramfile"
        backend_type = self._prepare_driver_from_backend(backend)
        self.run_params[f"check_state_{backend_type}s_vm1"] = "root"
        # bonus: test for two images rather than one
        self.run_params["images_vm1"] = "image1 image2"
        self.run_params["image_name_image1_vm1"] = "vm1/image1"
        self.run_params["image_name_image2_vm1"] = "vm1/image2"

        # assert root state is correctly detected
        for image_format in ["qcow2", "raw", "something-else"]:
            self.run_params["image_format"] = image_format
            with self.driver.mock_check("launch", backend_type, False, True) as driver:
                file_suffix = f".{image_format}" if image_format != "raw" else ""
                self.driver.exist_lambda = lambda filename: filename.endswith(file_suffix) or filename.endswith("vm1")
                exists = ss.check_states(self.run_params, self.env)
            self.assertTrue(exists)

        # assert root state is correctly not detected
        with self.driver.mock_check("launch", backend_type, False, False) as driver:
            exists = ss.check_states(self.run_params, self.env)
        self.assertFalse(exists)

    def test_get_root_image(self):
        """Test that root getting with an image state backend works."""
        # only test with most default backends
        backend = "qcow2"
        backend_type = self._prepare_driver_from_backend(backend)
        self.run_params[f"get_state_{backend_type}s_vm1"] = "root"

        # cannot verify that the operation is NOOP so simply run it for coverage
        with self.driver.mock_check("launch", backend_type, False, True) as driver:
            ss.get_states(self.run_params, self.env)

    @mock.patch('avocado_i2n.states.pool.shutil')
    def test_get_root_image_pool(self, mock_shutil):
        """Test that root getting with the pool backend works."""
        backend = "pool"
        backend_type = self._prepare_driver_from_backend(backend)
        self.run_params[f"get_state_{backend_type}s_vm1"] = "root"

        # consider local root with priority
        self.run_params["use_pool"] = "yes"
        mock_shutil.reset_mock()
        with self.driver.mock_check("launch", backend_type, False, True) as driver:
            ss.get_states(self.run_params, self.env)
        mock_shutil.copy.assert_not_called()

        # use pool root if enabled and no local root
        self.run_params["use_pool"] = "yes"
        mock_shutil.reset_mock()
        with self.driver.mock_check("launch", backend_type, False, False) as driver:
            self.driver.exist_lambda = lambda filename: filename.startswith("/data/pool")
            ss.get_states(self.run_params, self.env)
        mock_shutil.copy.assert_called_with("/data/pool/vm1/image.qcow2",
                                            "/images/vm1/image.qcow2")

        # do not use pool root if disabled and no local root
        self.run_params["use_pool"] = "no"
        mock_shutil.reset_mock()
        with self.driver.mock_check("launch", backend_type, False, False) as driver:
            self.driver.exist_lambda = lambda filename: filename.startswith("/data/pool")
            with self.assertRaises(exceptions.TestAbortError):
                ss.get_states(self.run_params, self.env)
        mock_shutil.copy.assert_not_called()

    def test_get_root_vm(self):
        """Test that root getting with an vm state backend works."""
        # only test with most default backends
        backend = "qcow2vt"
        backend_type = self._prepare_driver_from_backend(backend)
        self.run_params[f"get_state_{backend_type}s_vm1"] = "root"

        # cannot verify that the operation is NOOP so simply run it for coverage
        with self.driver.mock_check("launch", backend_type, False, True) as driver:
            ss.get_states(self.run_params, self.env)

    # TODO: LVM is not supposed to reach to QCOW2 but we have in-code TODO about it
    @mock.patch('avocado_i2n.states.setup.env_process', mock.Mock(return_value=0))
    @mock.patch('avocado_i2n.states.lvm.process')
    def test_set_root_image_lvm(self, mock_process):
        """Test that root setting with the LVM backend works."""
        backend = "lvm"
        backend_type = self._prepare_driver_from_backend(backend)
        self.run_params[f"set_state_{backend_type}s_vm1"] = "root"
        self.run_params["set_size_vm1"] = "30G"
        self.run_params["lv_pool_name"] = "thin_pool"
        self.run_params["lv_pool_size"] = "30G"
        self.run_params["lv_size"] = "30G"
        self.run_params["image_name_vm1"] = "vm1/image"
        self.run_params["disk_sparse_filename_vm1"] = "virtual_hdd_vm1"
        self.run_params["use_tmpfs"] = "yes"
        self.run_params["disk_basedir_vm1"] = "/tmp"
        self.run_params["disk_vg_size_vm1"] = "40000"
        self.run_params["image_raw_device_vm1"] = "no"
        # TODO: LVM is still internally tied to QCOW images and needs testing otherwise
        self.run_params["image_format"] = "qcow2"

        def process_run_side_effect(cmd, **kwargs):
            if cmd == "pvs":
                stdout = b"/dev/loop0 disk_vm1   lvm2"
            elif cmd == "losetup --find":
                stdout = b"/dev/loop0"
            elif cmd == "losetup --all":
                stdout = b"/dev/loop0: [0050]:2033 (/tmp/vm1_image1/virtual_hdd)"
            else:
                stdout = b""
            result = process.CmdResult(cmd, stdout=stdout, exit_status=0)
            return result
        mock_process.run.side_effect = process_run_side_effect
        mock_process.system.return_value = 0

        # assert root state is detected and overwritten
        mock_process.reset_mock()
        with self.driver.mock_check("launch", backend_type, False, True) as driver:
            ss.set_states(self.run_params, self.env)
            driver.lv_check.assert_called_with('disk_vm1', 'LogVol')
            driver.lv_create.assert_called_once_with('disk_vm1', 'LogVol', '30G', pool_name='thin_pool', pool_size='30G')
            driver.lv_take_snapshot.assert_called_once_with('disk_vm1', 'LogVol', 'current_state')
        mock_process.run.assert_called_with('vgcreate disk_vm1 /dev/loop0', sudo=True)

        # assert root state is not detected and created
        mock_process.reset_mock()
        with self.driver.mock_check("launch", backend_type, False, True) as driver:
            ss.set_states(self.run_params, self.env)
            driver.lv_check.assert_called_with('disk_vm1', 'LogVol')
            driver.lv_create.assert_called_once_with('disk_vm1', 'LogVol', '30G', pool_name='thin_pool', pool_size='30G')
            driver.lv_take_snapshot.assert_called_once_with('disk_vm1', 'LogVol', 'current_state')
        mock_process.run.assert_called_with('vgcreate disk_vm1 /dev/loop0', sudo=True)

    @mock.patch('avocado_i2n.states.setup.env_process')
    def test_set_root_image_qcow2(self, mock_env_process):
        """Test that root setting with the QCOW2 backend works."""
        backend = "qcow2"
        backend_type = self._prepare_driver_from_backend(backend)
        self.run_params[f"set_state_{backend_type}s_vm1"] = "root"

        # assert root state is detected and overwritten
        mock_env_process.reset_mock()
        with self.driver.mock_check("launch", backend_type, False, True) as driver:
            exists_times = [True, False]
            self.driver.exist_lambda = lambda filename: exists_times.pop(0)
            ss.set_states(self.run_params, self.env)
            self.mock_vms["vm1"].is_alive.assert_called()
            # called twice because QCOW2's set_root can only set missing root part
            # like only turning off the vm or only creating an image
            self.mock_file_exists.assert_called_with("/images/vm1/image.qcow2")
        mock_env_process.preprocess_image.assert_called_once()

        # assert root state is not detected and created
        mock_env_process.reset_mock()
        with self.driver.mock_check("launch", backend_type, False, False) as driver:
            ss.set_states(self.run_params, self.env)
            self.mock_vms["vm1"].is_alive.assert_called()
            # called twice because QCOW2's set_root can only set missing root part
            # like only turning off the vm or only creating an image
            self.mock_file_exists.assert_called_with("/images/vm1/image.qcow2")
        mock_env_process.preprocess_image.assert_called_once()

        # assert running vms result in setting only remaining part of root state
        mock_env_process.reset_mock()
        with self.driver.mock_check("launch", backend_type, False, True) as driver:
            self.mock_vms["vm1"].is_alive.return_value = True
            ss.set_states(self.run_params, self.env)
            # is vm is not alive root is not available and no need to check image existence
            self.mock_vms["vm1"].is_alive.assert_called()
            self.mock_vms["vm1"].destroy.assert_called_once_with(gracefully=True)
        mock_env_process.preprocess_image.assert_not_called()

    @mock.patch('avocado_i2n.states.pool.shutil')
    @mock.patch('avocado_i2n.states.pool.QCOW2Backend.set_root')
    @mock.patch('avocado_i2n.states.pool.QCOW2Backend.unset_root', mock.Mock())
    def test_set_root_image_pool(self, mock_check_root, mock_shutil):
        """Test that root setting with the pool backend works."""
        backend = "pool"
        backend_type = self._prepare_driver_from_backend(backend)
        self.run_params[f"set_state_{backend_type}s_vm1"] = "root"

        # not updating the state pool means setting the local root
        self.run_params["update_pool"] = "no"
        mock_check_root.reset_mock()
        mock_shutil.reset_mock()
        with self.driver.mock_check("launch", backend_type, False, True) as driver:
            ss.set_states(self.run_params, self.env)
        mock_check_root.assert_called_once()
        mock_shutil.copy.assert_not_called()

        # updating the state pool means not setting the local root
        self.run_params["update_pool"] = "yes"
        mock_check_root.reset_mock()
        mock_shutil.reset_mock()
        with self.driver.mock_check("launch", backend_type, False, True) as driver:
            ss.set_states(self.run_params, self.env)
        mock_check_root.assert_not_called()
        mock_shutil.copy.assert_called_with("/images/vm1/image.qcow2",
                                            "/data/pool/vm1/image.qcow2")

        # updating the state pool without local root must fail early
        self.run_params["update_pool"] = "yes"
        mock_check_root.reset_mock()
        mock_shutil.reset_mock()
        with self.driver.mock_check("launch", backend_type, False, True) as driver:
            self.driver.exist_lambda = lambda filename: filename.startswith("/data/pool")
            with self.assertRaises(RuntimeError):
                ss.set_states(self.run_params, self.env)
        mock_check_root.assert_not_called()
        mock_shutil.copy.assert_not_called()

    def test_set_root_vm(self):
        """Test that root setting with a vm state backend works."""
        backend = "qcow2vt"
        backend_type = self._prepare_driver_from_backend(backend)
        self.run_params[f"set_state_{backend_type}s_vm1"] = "root"

        # assert root state is not detected and created
        with self.driver.mock_check("launch", backend_type, False, False) as driver:
            ss.set_states(self.run_params, self.env)
            self.mock_vms["vm1"].create.assert_called_once_with()

        # assert root state is detected and but not overwritten in this case
        with self.driver.mock_check("launch", backend_type, False, True) as driver:
            ss.set_states(self.run_params, self.env)
            self.mock_vms["vm1"].create.assert_not_called()

    @mock.patch('avocado_i2n.states.lvm.vg_cleanup')
    def test_unset_root_image_lvm(self, mock_vg_cleanup):
        """Test that root unsetting with the LVM backend works."""
        backend = "lvm"
        backend_type = self._prepare_driver_from_backend(backend)
        self.run_params[f"unset_state_{backend_type}s_vm1"] = "root"
        self.run_params["image_name_vm1"] = "vm1/image"
        self.run_params["disk_sparse_filename_vm1"] = "virtual_hdd_vm1"
        self.run_params["use_tmpfs"] = "yes"
        self.run_params["disk_basedir_vm1"] = "/tmp"
        self.run_params["image_raw_device_vm1"] = "no"

        # assert root state is detected and removed
        mock_vg_cleanup.reset_mock()
        with self.driver.mock_check("launch", backend_type, False, True) as driver:
            ss.unset_states(self.run_params, self.env)
            driver.vg_check.assert_called_once_with('disk_vm1')
        mock_vg_cleanup.assert_called_once_with('virtual_hdd_vm1', '/tmp/disk_vm1', 'disk_vm1', None, True)

        # test tolerance to cleanup errors
        mock_vg_cleanup.reset_mock()
        mock_vg_cleanup.side_effect = exceptions.TestError("cleanup failed")
        with self.driver.mock_check("launch", backend_type, False, True) as driver:
            driver.vg_check.return_value = True
            ss.unset_states(self.run_params, self.env)
            driver.vg_check.assert_called_once_with('disk_vm1')
        mock_vg_cleanup.assert_called_once_with('virtual_hdd_vm1', '/tmp/disk_vm1', 'disk_vm1', None, True)

    @mock.patch('avocado_i2n.states.setup.os')
    @mock.patch('avocado_i2n.states.setup.env_process')
    def test_unset_root_image_qcow2(self, mock_env_process, mock_os):
        """Test that root unsetting with the QCOW2 backend works."""
        backend = "qcow2"
        backend_type = self._prepare_driver_from_backend(backend)
        self.run_params[f"unset_state_{backend_type}s_vm1"] = "root"

        # assert root state is detected and removed
        mock_os.reset_mock()
        mock_env_process.reset_mock()
        with self.driver.mock_check("launch", backend_type, False, True) as driver:
            ss.unset_states(self.run_params, self.env)
        mock_env_process.postprocess_image.assert_called_once()
        mock_os.rmdir.assert_called_once()

        # TODO: assert running vms result in not completely available root state
        # TODO: running vm implies no root state which implies ignore or abort policy
        #self.mock_vms["vm1"].destroy.assert_called_once_with(gracefully=False)
        pass

    @mock.patch('avocado_i2n.states.pool.os')
    @mock.patch('avocado_i2n.states.setup.env_process')
    def test_unset_root_image_pool(self, mock_env_process, mock_os):
        """Test that root unsetting with the pool backend works."""
        backend = "pool"
        backend_type = self._prepare_driver_from_backend(backend)
        self.run_params[f"unset_state_{backend_type}s_vm1"] = "root"

        # retore some path capabilities in our mock module
        mock_os.path.join = os.path.join
        mock_os.path.basename = os.path.basename

        # not updating the state pool means unsetting the local root
        self.run_params["update_pool"] = "no"
        mock_env_process.reset_mock()
        mock_os.reset_mock()
        with self.driver.mock_check("launch", backend_type, False, True) as driver:
            ss.unset_states(self.run_params, self.env)
        mock_env_process.postprocess_image.assert_called_once()
        mock_os.unlink.assert_not_called()

        # updating the state pool means not unsetting the local root
        self.run_params["update_pool"] = "yes"
        mock_env_process.reset_mock()
        mock_os.reset_mock()
        with self.driver.mock_check("launch", backend_type, False, True) as driver:
            ss.unset_states(self.run_params, self.env)
        mock_env_process.postprocess_image.assert_not_called()
        # TODO: partial (local only) root state implies no root and thus ignore unset only
        mock_os.unlink.assert_called_with("/data/pool/vm1/image.qcow2")

    def test_unset_root_vm(self):
        """Test that root unsetting with a vm state backend works."""
        backend = "qcow2vt"
        backend_type = self._prepare_driver_from_backend(backend)
        self.run_params[f"unset_state_{backend_type}s_vm1"] = "root"

        # assert root state is detected and removed
        with self.driver.mock_check("launch", backend_type, False, True) as driver:
            ss.unset_states(self.run_params, self.env)
            self.mock_vms["vm1"].destroy.assert_called_once_with(gracefully=False)

    def test_push(self):
        """Test that pushing with a state backend works."""
        backend = "lvm"
        backend_type = self._prepare_driver_from_backend(backend)
        self.run_params["push_state_images_vm1"] = "launch"
        self.run_params["push_mode_vm1"] = "ff"

        # assert state saving is skipped if reusable state is available
        with self.driver.mock_check("launch", backend_type, False) as driver:
            ss.push_states(self.run_params, self.env)
            self.driver.assert_set(driver, "launch", backend_type, 1)

        # test push disabled for root/boot states
        del self.run_params["push_state_images_vm1"]
        self.run_params["push_state_vm1"] = "root"
        with self.driver.mock_check("launch", backend_type, False) as driver:
            ss.push_states(self.run_params, self.env)
            driver.assert_not_called()
        self.run_params["push_state_vm1"] = "boot"
        with self.driver.mock_check("launch", backend_type, False) as driver:
            ss.push_states(self.run_params, self.env)
            driver.assert_not_called()

    def test_pop_image(self):
        """Test that popping with an image state backend works."""
        backend = "lvm"
        backend_type = self._prepare_driver_from_backend(backend)
        self.run_params["pop_state_images_vm1"] = "launch"
        self.run_params["image_name_vm1"] = "vm1/image"
        self.run_params["image_raw_device_vm1"] = "no"

        with self.driver.mock_check("launch", backend_type, True) as driver:
            ss.pop_states(self.run_params, self.env)
            # TODO: once called assertions are too strong
            #self.driver.assert_get(driver, "launch", backend_type, 1)
            #self.driver.assert_unset(driver, "launch", backend_type, 1)
            driver.lv_check.assert_called_with("disk_vm1", "launch")
            driver.lv_take_snapshot.assert_called_once_with('disk_vm1', 'launch', 'current_state')
            expected = [mock.call('disk_vm1', 'current_state'), mock.call('disk_vm1', 'launch')]
            self.assertListEqual(driver.lv_remove.call_args_list, expected)

        # test pop disabled for root/boot states
        del self.run_params["pop_state_images_vm1"]
        self.run_params["pop_state_vm1"] = "root"
        with self.driver.mock_check("launch", backend_type, False) as driver:
            ss.pop_states(self.run_params, self.env)
            driver.assert_not_called()
        self.run_params["pop_state_vm1"] = "boot"
        with self.driver.mock_check("launch", backend_type, False) as driver:
            ss.pop_states(self.run_params, self.env)
            driver.assert_not_called()

    def test_pop_vm(self):
        """Test that popping with a vm state backend works."""
        backend = "qcow2vt"
        backend_type = self._prepare_driver_from_backend(backend)
        self.run_params["pop_state_vms_vm1"] = "launch"

        with self.driver.mock_check("launch", backend_type, True) as driver:
            ss.pop_states(self.run_params, self.env)
            # TODO: once called assertions are too strong
            #self.driver.assert_get(driver, "launch", backend_type, 1)
            #self.driver.assert_unset(driver, "launch", backend_type, 1)
            driver.return_value.snapshot_list.assert_called_with(force_share=True)
            self.mock_vms["vm1"].is_alive.assert_called_with()
            self.mock_vms["vm1"].loadvm.assert_called_once_with('launch')
            self.mock_vms["vm1"].monitor.send_args_cmd.assert_called_once_with("delvm id=launch")

    @mock.patch('avocado_i2n.states.qcow2.QemuImg')
    @mock.patch('avocado_i2n.states.lvm.lv_utils')
    def test_check_multivm(self, mock_lv_utils, _mock_qemu_class):
        """Test that checking various states of multiple vms works."""
        self.run_params["vms"] = "vm1 vm2"
        self.run_params["check_state_images"] = "launch"
        self.run_params["check_state_images_vm2"] = "launcher"
        self.run_params["vg_name_vm1"] = "disk_vm1"
        self.run_params["vg_name_vm2"] = "disk_vm2"
        self.run_params["image_name_vm1"] = "vm1/image"
        self.run_params["image_name_vm2"] = "vm2/image"
        backend = "lvm"
        backend_type = self._prepare_driver_from_backend(backend)
        self.run_params["skip_types"] = "nets"
        self._set_vm_qcow2_params()

        mock_lv_utils.reset_mock()
        mock_lv_utils.lv_check.return_value = True
        exists = ss.check_states(self.run_params, self.env)
        expected = [mock.call("disk_vm1", "LogVol"),
                    mock.call("disk_vm1", "launch"),
                    mock.call("disk_vm2", "LogVol"),
                    mock.call("disk_vm2", "launcher")]
        self.assertListEqual(mock_lv_utils.lv_check.call_args_list, expected)
        self.assertTrue(exists)

        # break on first false state check
        mock_lv_utils.reset_mock()
        mock_lv_utils.lv_check.return_value = False
        exists = ss.check_states(self.run_params, self.env)
        expected = [mock.call("disk_vm1", "LogVol")]
        self.assertListEqual(mock_lv_utils.lv_check.call_args_list, expected)
        self.assertFalse(exists)

    @mock.patch('avocado_i2n.states.lvm.lv_utils')
    def test_get_multivm(self, mock_lv_utils):
        """Test that getting various states of multiple vms works."""
        self.run_params["vms"] = "vm1 vm2 vm3"
        self.run_params["get_state_images"] = "launch2"
        self.run_params["get_state_images_vm1"] = "launch1"
        self.run_params["get_state_vms_vm3"] = "launch3"
        self.run_params["get_mode_vm1"] = "rx"
        self.run_params["get_mode"] = "ii"
        self.run_params["vg_name_vm1"] = "disk_vm1"
        self.run_params["vg_name_vm2"] = "disk_vm2"
        self.run_params["vg_name_vm3"] = "disk_vm3"
        self.run_params["image_name_vm1"] = "vm1/image"
        self.run_params["image_name_vm2"] = "vm2/image"
        self.run_params["image_name_vm3"] = "vm3/image"
        self.run_params["image_raw_device"] = "no"
        backend = "lvm"
        backend_type = self._prepare_driver_from_backend(backend)
        self.run_params["skip_types"] = "nets"
        self._set_vm_qcow2_params()

        # test on/off switch as well
        with self.driver.mock_check("launch3", "vm", True) as driver:
            def lv_check_side_effect(_vgname, lvname):
                return True if lvname in ["LogVol", "launch1"] else False if lvname == "launch2" else False
            mock_lv_utils.lv_check.side_effect = lv_check_side_effect
            self.mock_vms["vm1"].is_alive.return_value = False
            self.mock_vms["vm3"].is_alive.return_value = True

            ss.get_states(self.run_params, self.env)

            self.mock_vms["vm3"].is_alive.assert_called_once_with()
            driver.return_value.snapshot_list.assert_called_once_with(force_share=True)

        expected = [mock.call("disk_vm1", "LogVol"),
                    mock.call("disk_vm1", "launch1"),
                    mock.call("disk_vm2", "LogVol"),
                    mock.call("disk_vm2", "launch2"),
                    mock.call("disk_vm3", "LogVol"),
                    mock.call("disk_vm3", "launch2")]
        self.assertListEqual(mock_lv_utils.lv_check.call_args_list, expected)

    # TODO: LVM is not supposed to reach to QCOW2 but we have in-code TODO about it
    @mock.patch('avocado_i2n.states.setup.env_process', mock.Mock(return_value=0))
    @mock.patch('avocado_i2n.states.qcow2.QemuImg')
    @mock.patch('avocado_i2n.states.lvm.lv_utils')
    @mock.patch('avocado_i2n.states.lvm.process')
    def test_set_multivm(self, mock_process, mock_lv_utils, _mock_qemu_class):
        """Test that setting various states of multiple vms works."""
        self.run_params["vms"] = "vm2 vm3 vm4"
        self.run_params["set_state_images"] = "launch2"
        self.run_params["set_state_images_vm2"] = "launch2"
        self.run_params["set_state_images_vm3"] = "root"
        self.run_params["set_state_images_vm4"] = "launch4"
        self.run_params["set_mode_vm2"] = "rx"
        self.run_params["set_mode_vm3"] = "ff"
        self.run_params["set_mode_vm4"] = "aa"
        self.run_params["lv_size"] = "30G"
        self.run_params["lv_pool_name"] = "thin_pool"
        self.run_params["lv_pool_size"] = "30G"
        self.run_params["vg_name_vm2"] = "disk_vm2"
        self.run_params["vg_name_vm3"] = "disk_vm3"
        self.run_params["vg_name_vm4"] = "disk_vm4"
        self.run_params["image_name_vm2"] = "vm2/image"
        self.run_params["image_name_vm3"] = "vm3/image"
        self.run_params["image_name_vm4"] = "vm4/image"
        self.run_params["image_format"] = "raw"
        self.run_params["disk_sparse_filename_vm3"] = "virtual_hdd_vm3"
        self.run_params["use_tmpfs"] = "yes"
        self.run_params["disk_basedir"] = "/tmp"
        self.run_params["disk_vg_size"] = "40000"
        self.run_params["skip_types"] = "nets/vms"
        backend = "lvm"
        backend_type = self._prepare_driver_from_backend(backend)

        self.driver.exist_switch = False
        def lv_check_side_effect(_vgname, lvname):
            return True if lvname in ["LogVol", "launch2"] else False if lvname == "launch4" else False
        mock_lv_utils.lv_check.side_effect = lv_check_side_effect
        mock_lv_utils.vg_check.return_value = False
        mock_process.run.return_value = process.CmdResult("dummy-command")

        with self.assertRaises(exceptions.TestAbortError):
            ss.set_states(self.run_params, self.env)

        expected = [mock.call("disk_vm2", "LogVol"),
                    mock.call("disk_vm2", "launch2"),
                    mock.call("disk_vm3", "LogVol"),
                    mock.call("disk_vm4", "LogVol"),
                    mock.call("disk_vm4", "launch4")]
        self.assertListEqual(mock_lv_utils.lv_check.call_args_list, expected)
        mock_process.run.assert_called_with('vgcreate disk_vm3 ', sudo=True)
        mock_lv_utils.lv_create.assert_called_once_with('disk_vm3', 'LogVol', '30G', pool_name='thin_pool', pool_size='30G')
        mock_lv_utils.lv_take_snapshot.assert_called_once_with('disk_vm3', 'LogVol', 'current_state')

    @mock.patch('avocado_i2n.states.qcow2.QemuImg')
    @mock.patch('avocado_i2n.states.lvm.lv_utils')
    def test_unset_multivm(self, mock_lv_utils, _mock_qemu_class):
        """Test that unsetting various states of multiple vms works."""
        self.run_params["vms"] = "vm1 vm4"
        self.run_params["unset_state_images_vm1"] = "root"
        self.run_params["unset_state_images_vm4"] = "launch4"
        self.run_params["unset_mode_vm1"] = "ra"
        self.run_params["unset_mode_vm4"] = "fi"
        self.run_params["lv_name"] = "LogVol"
        self.run_params["vg_name_vm1"] = "disk_vm1"
        self.run_params["vg_name_vm4"] = "disk_vm4"
        self.run_params["image_name_vm1"] = "vm1/image"
        self.run_params["image_name_vm4"] = "vm4/image"
        self.run_params["image_raw_device"] = "no"
        backend = "lvm"
        backend_type = self._prepare_driver_from_backend(backend)

        self.driver.exist_switch = False
        mock_lv_utils.lv_check.side_effect = self.driver._only_lvm_root_exists

        ss.unset_states(self.run_params, self.env)

        expected = [mock.call("disk_vm1", "LogVol"),
                    mock.call("disk_vm4", "LogVol"),
                    mock.call("disk_vm4", "launch4")]
        self.assertListEqual(mock_lv_utils.lv_check.call_args_list, expected)

    def test_qcow2_dash(self):
        """Test the special character suppot for the QCOW2 backends."""
        self.run_params["image_name"] = "vm1/image"

        for do in ["check", "get", "set", "unset"]:
            for state_type in ["images", "vms"]:
                backend = "qcow2" if state_type == "images" else "qcow2vt"
                backend_type = self._prepare_driver_from_backend(backend)
                self.run_params[f"{do}_state_{state_type}"] = "launch-ready_123"

                # check root state name format
                with self.driver.mock_check("launch-ready_123", backend_type) as driver:
                    ss.__dict__[f"{do}_states"](self.run_params, self.env)
                del self.run_params[f"{do}_state_{state_type}"]

                # check internal state name format
                self.run_params[f"{do}_state"] = "launch-ready_123"
                with self.driver.mock_check("launch-ready_123", backend_type) as driver:
                    ss.BACKENDS["qcow2"]().__getattribute__(do)(self.run_params, self.env)
                    ss.BACKENDS["qcow2vt"]().__getattribute__(do)(self.run_params, self.env)
                del self.run_params[f"{do}_state"]

    @mock.patch('avocado_i2n.states.qcow2.os.path.isfile')
    def test_qcow2_convert(self, mock_isfile):
        """Test auxiliary qcow2 module conversion functionality."""
        self.run_params["raw_image"] = "ext_image"
        # set a generic one not restricted to vm1
        self.run_params["image_name"] = "vm1/image"
        backend = "qcow2"
        backend_type = self._prepare_driver_from_backend(backend)

        mock_isfile.return_value = True
        with self.driver.mock_check("launch", backend_type, False, False) as driver:
            qcow2.convert_image(self.run_params)
            driver.return_value.convert.assert_called()
            # TODO: this is now fully external assertion beyond our mocks
            # 'qemu-img convert -c -p -O qcow2 "./ext_image" "/images/vm1/image.qcow2"'

        mock_isfile.return_value = False
        with self.driver.mock_check("launch", backend_type, False, False) as driver:
            with self.assertRaises(FileNotFoundError):
                qcow2.convert_image(self.run_params)
            driver.return_value.assert_not_called()

        mock_isfile.return_value = True
        with self.driver.mock_check("launch", backend_type, False, False) as driver:
            driver.CmdError = process.CmdError
            result = process.CmdResult("qemu-img convert", stderr=b'..."write" lock...', exit_status=0)
            driver.return_value.check.side_effect = process.CmdError(result=result)
            with self.assertRaises(process.CmdError):
                qcow2.convert_image(self.run_params)
            # no convert command was executed
            driver.return_value.check.assert_called_once()
            # TODO: this is now fully external assertion beyond our mocks
            # 'qemu-img check /images/vm1/image.qcow2'
            driver.return_value.assert_not_called()

    @mock.patch('avocado_i2n.states.pool.SKIP_LOCKS', False)
    @mock.patch('avocado_i2n.states.pool.fcntl')
    def test_pool_locks(self, mock_fcntl):
        """Test auxiliary pool module locks functionality."""
        self._set_image_pool_params()
        self._create_mock_vms()

        image_locked = False
        with pool.image_lock("./image.qcow2", timeout=1) as lock:
            mock_fcntl.lockf.assert_called_once()
            image_locked = True
            mock_fcntl.reset_mock()

            # TODO: from a different process if we decide to from unit tests to
            # functional tests that add more elaborate setup like this
            #with pool.image_lock("./image.qcow2", timeout=1) as lock:
            #    mock_fcntl.lockf.assert_called_once()
            #    mock_fcntl.reset_mock()
            #mock_fcntl.lockf.assert_called_once()
            #mock_fcntl.reset_mock()

        self.assertTrue(image_locked)
        mock_fcntl.lockf.assert_called_once()

    def test_skip_type(self):
        """Test that QCOW2 format filter for the QCOW2 backends."""
        backend = "qcow2"
        backend_type = self._prepare_driver_from_backend(backend)
        self.run_params["skip_types"] = "nets"
        self._set_vm_qcow2_params()

        for do in ["check", "get", "set", "unset"]:
            self.run_params[f"{do}_state"] = "launch"
            self.run_params["skip_types"] = "nets nets/vms nets/vms/images"

            with self.driver.mock_check("launch", backend_type, False, False) as driver:
                ss.__dict__[f"{do}_states"](self.run_params, self.env)
                driver.run.assert_not_called()
                self.mock_vms["vm1"].assert_not_called()


if __name__ == '__main__':
    unittest.main()
