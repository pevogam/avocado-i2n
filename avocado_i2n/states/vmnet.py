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
Module for the VMNet state management backend.

SUMMARY
------------------------------------------------------

Copyright: Intra2net AG

INTERFACE
------------------------------------------------------

"""

from typing import Any

from virttest.utils_params import Params

from .composition import NetStateBackend
from ..vmnet import VMNetwork


class VMNetBackend(NetStateBackend):
    """Backend manipulating network states as VMNet operations."""

    network_class = VMNetwork

    @classmethod
    def get(cls, params: Params, object: Any = None) -> None:
        """
        Retrieve a state disregarding the current changes.

        All arguments match the base class.
        """
        env = object
        env.start_ip_sniffing(params)
        vmn = cls.network_class(params, env)

        vmn.setup_host_bridges()
        vmn.setup_host_services()
        env.vmnet = vmn
        type(env).get_vmnet = lambda self: self.vmnet
