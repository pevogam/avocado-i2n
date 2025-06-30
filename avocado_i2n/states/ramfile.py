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

from .composition import VMStateBackend
from .pool import SourcedStateBackend


logging = log.getLogger("avocado.job." + __name__)


class RamfileBackend(SourcedStateBackend, VMStateBackend):
    """Backend manipulating vm states as ram dump files."""

    @classmethod
    def _show(cls, params: Params, object: Any = None) -> list[str]:
        """
        Return a list of available states of a specific type.

        All arguments match the base class.
        """
        return cls._show_driver(params, object)

    @classmethod
    def _show_driver(cls, params: Params, object: Any = None) -> list[str]:
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

        states = []
        images_states = super(SourcedStateBackend, cls).show(params, object=object)
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
        super(SourcedStateBackend, cls).get(params, object=object)

    @classmethod
    def _get_driver(cls, params: Params, object: Any = None) -> None:
        """
        Retrieve a state disregarding the current changes.

        All arguments match the base class.
        """
        vm = object
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
        super(SourcedStateBackend, cls).set(params, object=object)

    @classmethod
    def _set_driver(cls, params: Params, object: Any = None) -> None:
        """
        Store a state saving the current changes.

        All arguments match the base class.

        This only sets a state without necessarily restoring which could
        be invoked separately.
        """
        vm = object
        state_dir = params["swarm_pool"]
        vm_dir = os.path.join(state_dir, params["object_id"])
        state_file = os.path.join(vm_dir, params["set_state"] + ".state")
        if os.path.exists(state_file):
            os.unlink(state_file)
        vm.save_to_file(state_file)
        vm.destroy(gracefully=False)

    @classmethod
    def _unset(cls, params: Params, object: Any = None) -> None:
        """
        Remove a state with previous changes.

        All arguments match the base class.
        """
        super(SourcedStateBackend, cls).unset(params, object=object)

    @classmethod
    def _unset_driver(cls, params: Params, object: Any = None) -> None:
        """
        Remove a state with previous changes.

        All arguments match the base class.
        """
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
        state_dir = params["swarm_pool"]
        vm_dir = os.path.join(state_dir, params["object_id"])
        if not os.path.exists(vm_dir):
            vm_name = params["vms"]
            logging.debug("Checking whether %s's preconditions are satisfied", vm_name)
            logging.info(
                "The base directory for the virtual machine %s is missing", vm_name
            )
            return False

        return super(SourcedStateBackend, cls).check(params, object=object)

    @classmethod
    def initialize(cls, params: Params, object: Any = None) -> None:
        """
        Set a root state to provide running object.

        All arguments match the base class.
        """
        super(SourcedStateBackend, cls).initialize(params, object=object)
        state_dir = params["swarm_pool"]
        vm_dir = os.path.join(state_dir, params["object_id"])
        logging.info("Creating base directory for vm states %s", vm_dir)
        os.makedirs(vm_dir, exist_ok=True)

    @classmethod
    def finalize(cls, params: Params, object: Any = None) -> None:
        """
        Unset a root state to prevent object from running.

        All arguments match the base class.
        """
        super(SourcedStateBackend, cls).finalize(params, object=object)
        state_dir = params["swarm_pool"]
        vm_dir = os.path.join(state_dir, params["object_id"])
        logging.info("Removing base directory for vm states %s", vm_dir)
        os.rmdir(vm_dir)
