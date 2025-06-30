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
Module for the QCOW2 state management backends.

SUMMARY
------------------------------------------------------

Copyright: Intra2net AG

INTERFACE
------------------------------------------------------

"""

import glob
import os
import re
import json
import shutil
from typing import Any
import logging as log

from virttest import env_process
from virttest.qemu_storage import QemuImg
from virttest.utils_params import Params

from .composition import ImageStateBackend, VMStateBackend
from .pool import RootSourcedStateBackend, SourcedStateBackend


logging = log.getLogger("avocado.job." + __name__)


#: off qemu states regex (0 vm size)
QEMU_OFF_STATES_REGEX = re.compile(
    r"^\d+\s+([\w\.-]+)\s*(0 B)\s+\d{4}-\d\d-\d\d", flags=re.MULTILINE
)
#: on qemu states regex (>0 vm size)
QEMU_ON_STATES_REGEX = re.compile(
    r"^\d+\s+([\w\.-]+)\s*(?!0 B)(\d+e?[\-\+]?[\.\d]* \w+)\s+\d{4}-\d\d-\d\d",
    flags=re.MULTILINE,
)


class QCOW2Backend(ImageStateBackend, RootSourcedStateBackend):
    """Backend manipulating image states as internal QCOW2 snapshots."""

    @classmethod
    def show(cls, params: Params, object: Any = None) -> list[str]:
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
        qemu_img = QemuImg(params, params["images_base_dir"], params["images"])
        logging.debug(
            "Showing image internal states for image %s",
            params["images"],
        )
        on_snapshots_dump = qemu_img.snapshot_list(force_share=True)
        require_running_object = params.get_boolean("require_ram", False)
        pattern = (
            QEMU_ON_STATES_REGEX if require_running_object else QEMU_OFF_STATES_REGEX
        )
        state_tuples = re.findall(pattern, on_snapshots_dump)
        states = []
        for state_tuple in state_tuples:
            logging.debug(
                "Detected image state '%s' of size %s",
                state_tuple[0],
                state_tuple[1],
            )
            states.append(state_tuple[0])
        return states

    @classmethod
    def _get_driver(cls, params: Params, object: Any = None) -> None:
        """
        Retrieve a state disregarding the current changes.

        All arguments match the base class.
        """
        image = params["images"]
        params["image_chain"] = f"{image} snapshot"
        params["image_raw_device_snapshot"] = "yes"
        params["image_name_snapshot"] = params["get_state"]
        qemu_img = QemuImg(params, params["images_base_dir"], image)
        qemu_img.snapshot_apply()

    @classmethod
    def _set_driver(cls, params: Params, object: Any = None) -> None:
        """
        Store a state saving the current changes.

        All arguments match the base class.
        """
        image = params["images"]
        params["image_chain"] = f"{image} snapshot"
        params["image_raw_device_snapshot"] = "yes"
        params["image_name_snapshot"] = params["set_state"]
        qemu_img = QemuImg(params, params["images_base_dir"], image)
        qemu_img.snapshot_create()

    @classmethod
    def _unset_driver(cls, params: Params, object: Any = None) -> None:
        """
        Remove a state with previous changes.

        All arguments match the base class.
        """
        image = params["images"]
        params["image_chain"] = f"{image} snapshot"
        params["image_raw_device_snapshot"] = "yes"
        params["image_name_snapshot"] = params["unset_state"]
        qemu_img = QemuImg(params, params["images_base_dir"], image)
        qemu_img.snapshot_del()

    @classmethod
    def _check(cls, params: Params, object: Any = None) -> bool:
        """
        Check whether a root state or essentially the object exists locally.

        All arguments match the base class.
        """
        vm_name = params["vms"]
        image_name = params["image_name"]
        logging.debug(
            "Checking whether %s's %s exists (root state requested)",
            vm_name,
            image_name,
        )
        if not os.path.isabs(image_name):
            image_name = os.path.join(params["images_base_dir"], image_name)
        image_format = params.get("image_format", "qcow2")
        logging.debug("Checking for %s image %s", image_format, image_name)
        image_format = "" if image_format in ["raw", ""] else "." + image_format
        if os.path.exists(image_name + image_format):
            logging.info(
                "The required virtual machine %s's %s exists", vm_name, image_name
            )
            return True
        else:
            logging.info(
                "The required virtual machine %s's %s doesn't exist",
                vm_name,
                image_name,
            )
            return False

    @classmethod
    def _initialize(cls, params: Params, object: Any = None) -> None:
        """
        Set a root state to provide object existence.

        All arguments match the base class.
        """
        vm_name = params["vms"]
        if object is not None and object.is_alive():
            object.destroy(gracefully=params.get_boolean("soft_boot", True))
        image_name = params["image_name"]
        if not os.path.isabs(image_name):
            image_name = os.path.join(params["images_base_dir"], image_name)
        image_format = params.get("image_format")
        image_format = "" if image_format in ["raw", ""] else "." + image_format
        if not os.path.exists(image_name + image_format):
            os.makedirs(os.path.dirname(image_name), exist_ok=True)
            logging.info("Creating image %s for %s", image_name, vm_name)
            params.update({"create_image": "yes", "force_create_image": "yes"})
            env_process.preprocess_image(None, params, image_name)

    @classmethod
    def _finalize(cls, params: Params, object: Any = None) -> None:
        """
        Unset a root state to prevent object existence.

        All arguments match the base class.
        """
        vm_name = params["vms"]
        if object is not None and object.is_alive():
            object.destroy(gracefully=params.get_boolean("soft_boot", True))
        image_name = params["image_name"]
        if not os.path.isabs(image_name):
            image_name = os.path.join(params["images_base_dir"], image_name)
        logging.info("Removing image %s for %s", image_name, vm_name)
        params.update({"remove_image": "yes"})
        env_process.postprocess_image(None, params, image_name)
        try:
            os.rmdir(os.path.dirname(image_name))
        except OSError as error:
            logging.debug("Image directory not yet empty: %s", error)


class QCOW2ExtBackend(SourcedStateBackend, QCOW2Backend):
    """Backend manipulating image states as external QCOW2 snapshots."""

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
        vm_name, image_name = params["vms"], params["images"]
        vm_id = params["object_id"]
        state_dir = params["swarm_pool"]
        vm_dir = os.path.join(state_dir, vm_id)
        qemu_img = QemuImg(params, vm_dir, image_name)
        logging.debug(
            "Showing external states for %s image %s locally in %s",
            vm_name,
            image_name,
            state_dir,
        )
        image_dir = os.path.join(os.path.dirname(qemu_img.image_filename), image_name)
        if not os.path.exists(image_dir):
            return []
        snapshots = os.listdir(image_dir)
        states = []
        for snapshot in snapshots:
            if not snapshot.endswith(".qcow2"):
                continue
            size = os.stat(os.path.join(image_dir, snapshot)).st_size
            state = snapshot[:-6]
            logging.debug(
                f"Detected image state '{state}' of size "
                f"{round(size / 1024**3, 3)} GB ({size})"
            )
            states.append(state)
        return states

    @classmethod
    def _get(cls, params: Params, object: Any = None) -> None:
        """
        Retrieve a state disregarding the current changes.

        All arguments match the base class.
        """
        super(QCOW2Backend, cls).get(params, object)

    @classmethod
    def _get_driver(cls, params: Params, object: Any = None) -> None:
        """
        Retrieve a state disregarding the current changes.

        All arguments match the base class.
        """
        vm_name, image_name = params["vms"], params["images"]
        vm_id = params["object_id"]
        state = params["get_state"]

        logging.debug("Cleaning previous pointer and auxiliary data")
        QCOW2Backend._finalize(params, object)
        auxiliary_files = os.path.join(
            params["images_base_dir"], "*_" + params["image_name"] + "_qcow2_*"
        )
        for auxiliary_file in glob.glob(auxiliary_files):
            os.unlink(auxiliary_file)
        os.makedirs(params["images_base_dir"], exist_ok=True)

        state_dir = params["swarm_pool"]
        vm_dir = os.path.join(state_dir, vm_id)
        image_dir = os.path.join(vm_dir, image_name)
        params["image_chain"] = f"snapshot {image_name}"
        params["image_name_snapshot"] = os.path.join(image_dir, state)
        params["image_format_snapshot"] = "qcow2"
        qemu_img = QemuImg(
            params, os.path.join(params["vms_base_dir"], vm_name), image_name
        )
        qemu_img.create(params, ignore_errors=False)

    @classmethod
    def _set(cls, params: Params, object: Any = None) -> None:
        """
        Store a state saving the current changes.

        All arguments match the base class.
        """
        super(QCOW2Backend, cls).set(params, object)

    @classmethod
    def _set_driver(cls, params: Params, object: Any = None) -> None:
        """
        Store a state saving the current changes.

        All arguments match the base class.
        """
        vm_name, image_name = params["vms"], params["images"]
        vm_id = params["object_id"]
        state = params["set_state"]

        if not QCOW2Backend._check(params, object):
            raise RuntimeError(f"Missing head pointer image to commit as {state}")

        state_dir = params["swarm_pool"]
        vm_dir = os.path.join(state_dir, vm_id)
        image_dir = os.path.join(vm_dir, image_name)
        state_file = os.path.join(image_dir, state + ".qcow2")
        qemu_img = QemuImg(
            params, os.path.join(params["vms_base_dir"], vm_name), image_name
        )
        image_info = json.loads(qemu_img.info(output="json"))
        backing_file = image_info.get("backing-filename", "")
        inverse = params.copy()
        inverse["image_name"] = os.path.join(image_dir, state)
        if os.path.exists(state_file):
            qemu_img_inverse = QemuImg(
                inverse, os.path.join(params["vms_base_dir"], vm_name), image_name
            )
            image_info = json.loads(qemu_img_inverse.info(output="json"))
            inverse_file = image_info.get("backing-filename", "")
            if state_file == backing_file:
                # disallow loops and circular backing references, assuming an ancestor and squashing back
                logging.info(
                    f"Overwriting pre-existing backing state {state} via committing"
                )
                qemu_img.commit(params)
            elif inverse_file == backing_file:
                logging.info(
                    f"Overwriting pre-existing backing state {state} via forward replacement"
                )
                os.unlink(state_file)
                shutil.copy(qemu_img.image_filename, state_file)
            else:
                raise RuntimeError(
                    "Cannot perform nontrivial pre-existing state overwrite for qcow2ext"
                )
        else:
            shutil.copy(qemu_img.image_filename, state_file)

    @classmethod
    def _unset(cls, params: Params, object: Any = None) -> None:
        """
        Remove a state with previous changes.

        All arguments match the base class.
        """
        super(QCOW2Backend, cls).unset(params, object)

    @classmethod
    def _unset_driver(cls, params: Params, object: Any = None) -> None:
        """
        Remove a state with previous changes.

        All arguments match the base class.
        """
        vm_name, image_name = params["vms"], params["images"]
        vm_id = params["object_id"]
        state = params["unset_state"]

        logging.debug("Cleaning previous pointer and auxiliary data")
        QCOW2Backend._finalize(params, object)
        auxiliary_files = os.path.join(
            params["images_base_dir"], "*_" + params["image_name"] + "_qcow2_*"
        )
        for auxiliary_file in glob.glob(auxiliary_files):
            os.unlink(auxiliary_file)
        try:
            os.rmdir(params["images_base_dir"])
        except (OSError, FileNotFoundError) as error:
            logging.warning(
                "Could not remove images base directory %s: %s",
                params["images_base_dir"],
                error,
            )

        state_dir = params["swarm_pool"]
        vm_dir = os.path.join(state_dir, vm_id)
        image_dir = os.path.join(vm_dir, image_name)
        # TODO: the qemu img could become useful if we implement the below todo
        # qemu_img = QemuImg(params, os.path.join(params["vms_base_dir"], vm_name), image_name)
        # TODO: should we move to pointer image in case removed state is in backing chain?
        os.unlink(os.path.join(image_dir, state + ".qcow2"))

    @classmethod
    def check(cls, params: Params, object: Any = None) -> bool:
        """
        Check whether a root state or essentially the object exists locally.

        All arguments match the base class.
        """
        vm_id, image_name = params["object_id"], params["images"]
        state_dir = params["swarm_pool"]
        vm_dir = os.path.join(state_dir, vm_id)
        image_dir = os.path.join(vm_dir, image_name)
        if not os.path.exists(image_dir):
            logging.info("The base directory for the image %s is missing", image_name)
            return False
        return True

    @classmethod
    def initialize(cls, params: Params, object: Any = None) -> None:
        """
        Set a root state to provide object existence.

        All arguments match the base class.
        """
        vm_id, image_name = params["object_id"], params["images"]
        state_dir = params["swarm_pool"]
        vm_dir = os.path.join(state_dir, vm_id)
        image_dir = os.path.join(vm_dir, image_name)
        logging.info("Creating base directory for image %s", image_name)
        os.makedirs(image_dir, exist_ok=True)

    @classmethod
    def finalize(cls, params: Params, object: Any = None) -> None:
        """
        Unset a root state to prevent object existence.

        All arguments match the base class.
        """
        vm_id, image_name = params["object_id"], params["images"]
        state_dir = params["swarm_pool"]
        vm_dir = os.path.join(state_dir, vm_id)
        image_dir = os.path.join(vm_dir, image_name)
        logging.info("Removing base directory for image %s", image_name)
        os.rmdir(image_dir)


class QCOW2VTBackend(QCOW2Backend, VMStateBackend):
    """Backend manipulating vm states as QCOW2 snapshots using VT's VM bindings."""

    @classmethod
    def show(cls, params: Params, object: Any = None) -> list[str]:
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
        logging.debug(f"Showing vm states for vm {params['vms']}")
        states = None
        for i, image_name in enumerate(params.objects("images")):
            image_params = params.object_params(image_name)
            # TODO: refine method arguments by providing at least the image name directly
            image_params["images"] = image_name
            # the first image also contains the stored RAM for a vm state
            image_params["require_ram"] = "yes" if i == 0 else "no"
            image_states = super(QCOW2Backend, cls).show(image_params, object=object)
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
            raise ValueError("Need an environmental object to boot")
        if not vm.is_alive():
            vm.create()

        cls._get_driver(params, object)

    @classmethod
    def _get_driver(cls, params: Params, object: Any = None) -> None:
        """
        Retrieve a state disregarding the current changes.

        All arguments match the base class.
        """
        vm = object
        vm.pause()
        vm.loadvm(params["get_state"])
        vm.resume(timeout=3)

    @classmethod
    def set(cls, params: Params, object: Any = None) -> None:
        """
        Store a state saving the current changes.

        All arguments match the base class.
        """
        vm, vm_name = object, params["vms"]
        logging.info("Setting vm state '%s' of %s", params["set_state"], vm_name)

        if vm is None or not vm.is_alive():
            raise RuntimeError("No booted vm and thus vm state to set")

        cls._set_driver(params, object)

    @classmethod
    def _set_driver(cls, params: Params, object: Any = None) -> None:
        """
        Store a state saving the current changes.

        All arguments match the base class.
        """
        vm = object
        vm.pause()
        vm.savevm(params["set_state"])
        vm.resume(timeout=3)

    @classmethod
    def _unset_driver(cls, params: Params, object: Any = None) -> None:
        """
        Store a state saving the current changes.

        All arguments match the base class.

        Since all qcow2 images are removed there is nothing else to do and
        we can simply reuse the public vm state backend unset method.
        """
        pass

    @classmethod
    def _check(cls, params: Params, object: Any = None) -> bool:
        """
        Check whether a root state or essentially the object is running.

        All arguments match the base class.
        """
        return super(RootSourcedStateBackend, cls).check(params, object=object)

    @classmethod
    def _initialize(cls, params: Params, object: Any = None) -> None:
        """
        Set a root state to provide running object.

        All arguments match the base class.

        ..todo:: Study better the environment pre/postprocessing details necessary
                 for flawless vm destruction and creation to improve these.
        """
        super(RootSourcedStateBackend, cls).initialize(params, object=object)

    @classmethod
    def _finalize(cls, params: Params, object: Any = None) -> None:
        """
        Unset a root state to prevent object from running.

        All arguments match the base class.
        """
        super(RootSourcedStateBackend, cls).finalize(params, object=object)


def get_image_path(params: Params) -> str:
    """
    Get the absolute path to a QCOW2 image.

    :param params: configuration parameters
    :returns: absolute path to the QCOW2 image
    """
    image_name = params["image_name"]
    image_format = params.get("image_format")
    if image_format is None:
        raise ValueError(
            f"Unspecified image format for {image_name} - " "must be qcow2 or raw"
        )
    if image_format not in ["raw", "qcow2"]:
        raise ValueError(
            f"Incompatible image format {image_format} for"
            f" {image_name} - must be qcow2 or raw"
        )
    if not os.path.isabs(image_name):
        image_name = os.path.join(params["images_base_dir"], image_name)
    image_format = "" if image_format == "raw" else "." + image_format
    image_path = image_name + image_format
    return image_path


def convert_image(params: Params) -> None:
    """
    Convert a raw img to a QCOW2 or other image usable for virtual machines.

    :param params: configuration parameters
    :raises: py:class:`FileNotFoundError` if the source image doesn't exist
    :raises: py:class:`AssertionError` when the source image is in use

    .. note:: This function could be used with qemu-img for more general images
        and not just the QCOW2 format.
    """
    raw_directory = params.get("raw_image_dir", ".")
    raw_image = params["raw_image"]
    # allow the user to specify a path prefix for image files
    logging.info(f"Using image prefix {raw_directory}")
    source_image = os.path.join(raw_directory, raw_image)
    params.update(
        {
            "image_name_rawimg1": source_image,
            "image_format_rawimg1": "raw",
            "image_raw_device_rawimg1": "yes",
        }
    )
    source_qemu_img = QemuImg(params, raw_directory, "rawimg1")

    if not os.path.isfile(source_image):
        raise FileNotFoundError(f"Source image {source_image} doesn't exist")

    target_qemu_img = QemuImg(params, params["images_base_dir"], params["images"])
    target_image = get_image_path(params)
    # create the target directory if needed
    target_directory = os.path.dirname(target_image)
    os.makedirs(target_directory, exist_ok=True)

    if os.path.isfile(target_image):
        logging.debug(f"{target_image} already exists, checking if it's in use")
        result = target_qemu_img.check(params, params["images_base_dir"])
        if result.exit_status == 0:
            logging.debug(f"{target_image} not in use, integrity asserted")
        else:
            if '"write" lock' in result.stderr_text:
                logging.error(f"{target_image} is in use, refusing to convert")
                raise
            logging.debug(f"{target_image} exists but cannot check integrity")
        logging.info(f"Overwriting existing {target_image}")

    params["convert_target"] = params["images"]
    params["convert_compressed"] = "yes"
    source_qemu_img.convert(params, params["images_base_dir"])

    logging.debug("Conversion successful")
