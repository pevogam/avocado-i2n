# Copyright 2013-2025 Intranet AG and contributors
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
Module for the composed state management backends.

SUMMARY
------------------------------------------------------

Copyright: Intra2net AG

INTERFACE
------------------------------------------------------

"""

import os
from typing import Any
import logging as log

from virttest.utils_params import Params


logging = log.getLogger("avocado.job." + __name__)


class StateBackend:
    """A general backend implementing state manipulation."""

    @classmethod
    def show(cls, params: dict[str, str], object: Any = None) -> list[str]:
        """
        Return a list of available states of a specific type.

        :param params: configuration parameters
        :param object: object whose states are manipulated
        :returns: list of detected states
        """
        raise NotImplementedError("Cannot use abstract state backend")

    @classmethod
    def get(cls, params: dict[str, str], object: Any = None) -> None:
        """
        Retrieve a state disregarding the current changes.

        :param params: configuration parameters
        :param object: object whose states are manipulated
        """
        raise NotImplementedError("Cannot use abstract state backend")

    @classmethod
    def set(cls, params: dict[str, str], object: Any = None) -> None:
        """
        Store a state saving the current changes.

        :param params: configuration parameters
        :param object: object whose states are manipulated
        """
        raise NotImplementedError("Cannot use abstract state backend")

    @classmethod
    def unset(cls, params: dict[str, str], object: Any = None) -> None:
        """
        Remove a state with previous changes.

        :param params: configuration parameters
        :param object: object whose states are manipulated
        """
        raise NotImplementedError("Cannot use abstract state backend")

    @classmethod
    def _show_driver(cls, params: Params, object: Any = None) -> list[str]:
        """
        Define private imperative boundary version of show().

        All arguments match the base class.
        """
        raise NotImplementedError("Need a private imperative boundary")

    @classmethod
    def _get_driver(cls, params: Params, object: Any = None) -> list[str]:
        """
        Define private imperative boundary version of get().

        All arguments match the base class.
        """
        raise NotImplementedError("Need a private imperative boundary")

    @classmethod
    def _set_driver(cls, params: Params, object: Any = None) -> list[str]:
        """
        Define private imperative boundary version of set().

        All arguments match the base class.
        """
        raise NotImplementedError("Need a private imperative boundary")

    @classmethod
    def _unset_driver(cls, params: Params, object: Any = None) -> list[str]:
        """
        Define private imperative boundary version of unset().

        All arguments match the base class.
        """
        raise NotImplementedError("Need a private imperative boundary")

    @classmethod
    def check(cls, params: dict[str, str], object: Any = None) -> bool:
        """
        Check whether a root state or essentially the object exists.

        :param params: configuration parameters
        :param object: object whose states are manipulated
        :returns: whether the object (root state) is exists
        """
        raise NotImplementedError("Cannot use abstract state backend")

    @classmethod
    def initialize(cls, params: dict[str, str], object: Any = None) -> None:
        """
        Set a root state to provide object existence.

        :param params: configuration parameters
        :param object: object whose states are manipulated
        """
        raise NotImplementedError("Cannot use abstract state backend")

    @classmethod
    def finalize(cls, params: dict[str, str], object: Any = None) -> None:
        """
        Unset a root state to prevent object existence.

        :param params: configuration parameters
        :param object: object whose states are manipulated
        """
        raise NotImplementedError("Cannot use abstract state backend")


class ImageStateBackend(StateBackend):
    """
    A backend for image states.

    This class is used to manipulate image states in a consistent way.
    It is expected to be extended by specific image state backends.
    """

    @classmethod
    def switch_off(cls, mode: str, object: Any = None) -> None:
        """
        Switch vm or other object off if a non-running object is required.

        :param mode: how to switch off - "soft", "hard", or "none"
        :param object: the object to switch off
        """
        if mode not in ["soft", "hard", "none"]:
            raise ValueError(
                f"Invalid switch mode {mode} - must be soft, hard, or none"
            )

        vm = object
        if vm is None or not vm.is_alive():
            logging.warning("Will not switch off vm that is not available or alive")
            return
        if mode == "none":
            raise RuntimeError("The vm is alive and it shouldn't be")

        logging.info("The vm %s is running, switching it off", vm.name)
        vm.destroy(gracefully=mode == "soft")

    @classmethod
    def switch_on(cls, mode: str, object: Any = None) -> None:
        """
        Switch vm or other object on if a non-running object is required.

        :param mode: how to switch off - "soft", "hard", or "none"
        :param object: the object to switch on
        """
        if mode not in ["soft", "hard", "none"]:
            raise ValueError(
                f"Invalid switch mode {mode} - must be soft, hard, or none"
            )

        vm = object
        if mode == "none":
            return
        if vm is None or vm.is_alive():
            logging.warning("Will not switch on vm that is not available or alive")
            return

        logging.info("Starting the vm %s after image state operation", vm.name)
        vm.create()

    @classmethod
    def get(cls, params: Params, object: Any = None) -> None:
        """
        Retrieve a state disregarding the current changes.

        All arguments match the base class.
        """
        vm_name, image_name = params["vms"], params["images"]
        vm = object
        state, switch = params["get_state"], params["get_switch"]
        logging.info(
            "Reusing image state '%s' of %s/%s",
            state,
            vm_name,
            image_name,
        )

        cls.switch_off(switch, vm)

        cls._get_driver(params, object)

        cls.switch_on(switch, vm)

    @classmethod
    def set(cls, params: Params, object: Any = None) -> None:
        """
        Store a state saving the current changes.

        All arguments match the base class.
        """
        vm_name, image_name = params["vms"], params["images"]
        vm = object
        state, switch = params["set_state"], params["set_switch"]
        logging.info(
            "Creating image state '%s' of %s/%s",
            state,
            vm_name,
            image_name,
        )

        cls.switch_off(switch, vm)

        cls._set_driver(params, object)

        cls.switch_on(switch, vm)

    @classmethod
    def unset(cls, params: Params, object: Any = None) -> None:
        """
        Remove a state with previous changes.

        All arguments match the base class and in addition:
        """
        vm_name, image_name = params["vms"], params["images"]
        vm = object
        state, switch = params["unset_state"], params["unset_switch"]
        logging.info(
            "Removing image state '%s' of %s/%s",
            state,
            vm_name,
            image_name,
        )

        cls.switch_off(switch, vm)

        cls._unset_driver(params, object)

        cls.switch_on(switch, vm)


class VMStateBackend(StateBackend):
    """
    A backend for vm states.

    This class is used to manipulate vm states in a consistent way.
    It is expected to be extended by specific vm state backends.
    """

    image_state_backend = None

    @classmethod
    def show(cls, params: Params, object: Any = None) -> list[str]:
        """
        Return a list of available states of a specific type.

        All arguments match the base class.
        """
        logging.debug(f"Showing vm states for vm {params['vms']}")
        states = None
        for image_name in params.objects("images"):
            image_params = params.object_params(image_name)
            # TODO: refine method arguments by providing at least the image name directly
            image_params["images"] = image_name
            image_states = cls.image_state_backend.show(image_params, object=object)
            if states is None:
                states = set(image_states)
            else:
                states = states.intersection(image_states)
        return list(states)

    @classmethod
    def get(cls, params: Params, object: Any = None) -> None:
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

        cls._get_driver(params, object)

    @classmethod
    def set(cls, params: Params, object: Any = None) -> None:
        """
        Store a state saving the current changes.

        All arguments match the base class.
        """
        vm, vm_name = object, params["vms"]
        logging.info("Creating vm state '%s' of %s", params["set_state"], vm_name)

        if vm is None or not vm.is_alive():
            raise RuntimeError("No booted vm and thus vm state to set")
        vm.pause()

        cls._set_driver(params, object)

        for image_name in params.objects("images"):
            image_params = params.object_params(image_name)
            # TODO: refine method arguments by providing at least the image name directly
            image_params["images"] = image_name
            image_params["set_switch"] = "none"
            cls.image_state_backend.set(image_params, vm)

    @classmethod
    def unset(cls, params: Params, object: Any = None) -> None:
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

        cls._unset_driver(params, object)

    @classmethod
    def check(cls, params: Params, object: Any = None) -> bool:
        """
        Check whether a root state or essentially the object is running.

        All arguments match the base class.
        """
        vm_name = params["vms"]
        logging.debug("Checking whether %s's preconditions are satisfied", vm_name)

        # we cannot use local image backend because root conditions here require running vm
        for image_name in params.objects("images"):
            image_params = params.object_params(image_name)
            if not cls.image_state_backend.check(image_params):
                return False
        return True

    @classmethod
    def initialize(cls, params: Params, object: Any = None) -> None:
        """
        Set a root state to provide running object.

        All arguments match the base class.
        """
        vm_name = params["vms"]
        logging.info("Initializing preconditions for %s", vm_name)

        for image_name in params.objects("images"):
            image_params = params.object_params(image_name)
            cls.image_state_backend.initialize(image_params)

    @classmethod
    def finalize(cls, params: Params, object: Any = None) -> None:
        """
        Unset a root state to prevent object from running.

        All arguments match the base class.
        """
        vm_name = params["vms"]
        logging.info("Finalizing preconditions for %s", vm_name)

        for image_name in params.objects("images"):
            image_params = params.object_params(image_name)
            cls.image_state_backend.finalize(image_params)


class NetStateBackend(StateBackend):
    """
    A backend for net states.

    This class is used to manipulate net states in a consistent way.
    It is expected to be extended by specific net state backends.
    """

    @classmethod
    def show(cls, params: Params, object: Any = None) -> list[str]:
        """
        Return a list of available states of a specific type.

        All arguments match the base class.
        """
        return ["default"]

    @classmethod
    def set(cls, params: Params, object: Any = None) -> None:
        """
        Store a state saving the current changes.

        All arguments match the base class.
        """
        pass

    @classmethod
    def unset(cls, params: Params, object: Any = None) -> None:
        """
        Remove a state with previous changes.

        All arguments match the base class.
        """
        pass

    @classmethod
    def check(cls, params: Params, object: Any = None) -> bool:
        """
        Check whether a root state or essentially the object exists.

        All arguments match the base class.
        """
        return True

    @classmethod
    def initialize(cls, params: Params, object: Any = None) -> None:
        """
        Set a root state to provide object existence.

        All arguments match the base class.
        """
        pass

    @classmethod
    def finalize(cls, params: Params, object: Any = None) -> None:
        """
        Unset a root state to prevent object existence.

        All arguments match the base class and in addition:
        """
        pass
