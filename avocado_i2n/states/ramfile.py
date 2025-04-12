# Copyright 2013-2021 Intranet AG and contributors
#
# avocado-i2n is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# avocado-i2n is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with avocado-i2n.  If not, see <http://www.gnu.org/licenses/>.

"""
Module for the ramfile state management backend.

SUMMARY
------------------------------------------------------

Copyright: Intra2net AG

INTERFACE
------------------------------------------------------

"""

import os
from typing import Any
import logging as log

from virttest import env_process
from virttest.virt_vm import VMCreateError
from virttest.utils_params import Params

from .pool import SourcedStateBackend


logging = log.getLogger("avocado.job." + __name__)


class RamfileBackend(SourcedStateBackend):
    """Backend manipulating vm states as ram dump files."""

    image_state_backend = None

    @classmethod
    def _show(cls, params: Params, object: Any = None) -> list[str]:
        """
        Return a list of available states of a specific type.

        All arguments match the base class.
        """
        state_dir = params["swarm_pool"]
        logging.debug(
            f"Showing external states for vm {params['vms']} locally in {state_dir}"
        )
        vm_dir = os.path.join(state_dir, params["object_id"])
        snapshots = os.listdir(vm_dir)

        images_states = set()
        for image_name in params.objects("images"):
            image_params = params.object_params(image_name)
            # TODO: refine method arguments by providing at least the image name directly
            image_params["images"] = image_name
            image_snapshots = cls.image_state_backend.show(image_params, object=object)
            if len(images_states) == 0:
                images_states = set(image_snapshots)
            else:
                images_states = images_states.intersection(image_snapshots)

        states = []
        for snapshot in snapshots:
            if not snapshot.endswith(".state"):
                continue
            size = os.stat(os.path.join(vm_dir, snapshot)).st_size
            state = snapshot[:-6]
            logging.debug(
                f"Detected memory state '{snapshot}' of size "
                f"{round(size / 1024**3, 3)} GB ({size})"
            )
            if state in images_states:
                logging.debug(f"Memory state '{snapshot}' is a complete vm state")
                states.append(state)
        return states

    @classmethod
    def _get(cls, params: Params, object: Any = None) -> None:
        """
        Retrieve a state disregarding the current changes.

        All arguments match the base class.
        """
        vm, vm_name = object, params["vms"]
        logging.info("Reusing vm state '%s' of %s", params["get_state"], vm_name)

        if vm is None:
            raise ValueError("Need an environmental object to restore from file")
        if vm.is_alive():
            vm.destroy(gracefully=False)

        for image_name in params.objects("images"):
            image_params = params.object_params(image_name)
            # TODO: refine method arguments by providing at least the image name directly
            image_params["images"] = image_name
            image_params["get_switch"] = "none"
            cls.image_state_backend.get(image_params, vm)

        state_dir = params["swarm_pool"]
        vm_dir = os.path.join(state_dir, params["object_id"])
        state_file = os.path.join(vm_dir, params["get_state"] + ".state")
        vm.restore_from_file(state_file)
        vm.resume(timeout=3)

    @classmethod
    def _set(cls, params: Params, object: Any = None) -> None:
        """
        Store a state saving the current changes.

        All arguments match the base class.
        """
        vm, vm_name = object, params["vms"]
        logging.info("Setting vm state '%s' of %s", params["set_state"], vm_name)

        if vm is None or not vm.is_alive():
            raise RuntimeError("No booted vm and thus vm state to set")

        vm.pause()

        state_dir = params["swarm_pool"]
        vm_dir = os.path.join(state_dir, params["object_id"])
        state_file = os.path.join(vm_dir, params["set_state"] + ".state")
        if os.path.exists(state_file):
            os.unlink(state_file)
        vm.save_to_file(state_file)
        vm.destroy(gracefully=False)

        for image_name in params.objects("images"):
            image_params = params.object_params(image_name)
            # TODO: refine method arguments by providing at least the image name directly
            image_params["images"] = image_name
            image_params["set_switch"] = "none"
            cls.image_state_backend.set(image_params, vm)

        # BUG: because the built-in functionality uses system_reset
        # which leads to unclean file systems in some cases it is
        # better to restore from the saved state
        vm.restore_from_file(state_file)
        vm.resume(timeout=3)

    @classmethod
    def _unset(cls, params: Params, object: Any = None) -> None:
        """
        Remove a state with previous changes.

        All arguments match the base class.
        """
        vm, vm_name = object, params["vms"]
        logging.info("Removing vm state '%s' of %s", params["unset_state"], vm_name)

        for image_name in params.objects("images"):
            image_params = params.object_params(image_name)
            # TODO: refine method arguments by providing at least the image name directly
            image_params["images"] = image_name
            image_params["unset_switch"] = "none"
            cls.image_state_backend.unset(image_params, vm)

        state_dir = params["swarm_pool"]
        vm_dir = os.path.join(state_dir, params["object_id"])
        state_file = os.path.join(vm_dir, params["unset_state"] + ".state")
        os.unlink(state_file)

    @classmethod
    def check(cls, params: Params, object: Any = None) -> bool:
        """
        Check whether a root state or essentially the object is running.

        All arguments match the base class.
        """
        vm_name = params["vms"]
        logging.debug("Checking whether %s's root state is fully available", vm_name)

        state_dir = params["swarm_pool"]
        vm_dir = os.path.join(state_dir, params["object_id"])
        if not os.path.exists(vm_dir):
            logging.info(
                "The base directory for the virtual machine %s is missing", vm_name
            )
            return False

        for image_name in params.objects("images"):
            image_params = params.object_params(image_name)
            if not RamfileBackend.image_state_backend.check(image_params):
                return False
        return True

    @classmethod
    def initialize(cls, params: Params, object: Any = None) -> None:
        """
        Set a root state to provide running object.

        All arguments match the base class.
        """
        vm_name = params["vms"]
        state_dir = params["swarm_pool"]
        vm_dir = os.path.join(state_dir, params["object_id"])
        os.makedirs(vm_dir, exist_ok=True)

        for image_name in params.objects("images"):
            logging.info(
                f"Creating image {image_name} in order to boot {vm_name}",
            )
            image_params = params.object_params(image_name)
            RamfileBackend.image_state_backend.initialize(image_params)

    @classmethod
    def finalize(cls, params: Params, object: Any = None) -> None:
        """
        Unset a root state to prevent object from running.

        All arguments match the base class.
        """
        vm_name = params["vms"]
        for image_name in params.objects("images"):
            logging.info(
                f"Remove image {image_name} in order to remove all vm states of {vm_name}",
            )
            image_params = params.object_params(image_name)
            RamfileBackend.image_state_backend.finalize(image_params)
