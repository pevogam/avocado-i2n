# Copyright 2013-2020 Intranet AG and contributors
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

SUMMARY
------------------------------------------------------
Utility for the main test suite substructures like test nodes.

Copyright: Intra2net AG


INTERFACE
------------------------------------------------------

"""

import os
import re
from functools import cmp_to_key
import logging as log
logging = log.getLogger('avocado.job.' + __name__)

from aexpect.exceptions import ShellCmdError, ShellTimeoutError
from aexpect import remote
from aexpect import remote_door as door
from avocado.core.test_id import TestID
from avocado.core.nrunner.runnable import Runnable

from . import TestWorker, NetObject


door.DUMP_CONTROL_DIR = "/tmp"


class PrefixTreeNode(object):
    def __init__(self, variant=None, parent=None):
        self.variant = variant
        self.parent = parent
        self.end_test_node = None
        self.children = {}

    def check_child(self, variant):
        return variant in self.children

    def get_child(self, variant):
        return self.children[variant]

    def set_child(self, variant, child):
        self.children[variant] = child

    def unset_child(self, variant):
        del self.children[variant]

    def traverse(self):
        yield self
        for child in self.children.values():
            yield from child.traverse()


class PrefixTree(object):
    def __init__(self):
        self.variant_nodes = {}

    def __contains__(self, name: str) -> bool:
        variants = name.split(".")
        if variants[0] not in self.variant_nodes:
            return False
        for current in self.variant_nodes[variants[0]]:
            for variant in variants[1:]:
                if not current.check_child(variant):
                    break
                current = current.get_child(variant)
            else:
                return True
        return False

    def insert(self, test_node: "TestNode") -> None:
        variants = test_node.params["name"].split(".")
        if variants[0] not in self.variant_nodes.keys():
            self.variant_nodes[variants[0]] = [PrefixTreeNode(variants[0])]
        for current in self.variant_nodes[variants[0]]:
            for variant in variants[1:]:
                if not current.check_child(variant):
                    new_child = PrefixTreeNode(variant)
                    current.set_child(variant, new_child)
                    if variant not in self.variant_nodes:
                        self.variant_nodes[variant] = []
                    self.variant_nodes[variant] += [new_child]
                current = current.get_child(variant)
            current.end_test_node = test_node

    def get(self, name: str) -> list["TestNode"]:
        variants = name.split(".")
        if variants[0] not in self.variant_nodes:
            return []
        test_nodes = []
        for current in self.variant_nodes[variants[0]]:
            for variant in variants[1:]:
                if not current.check_child(variant):
                    break
                current = current.get_child(variant)
            else:
                for node in current.traverse():
                    if node.end_test_node is not None:
                        test_nodes.append(node.end_test_node)
        return test_nodes


class EdgeRegister():

    def __init__(self):
        self._registry = {}

    def __repr__(self):
        return f"[edge] registry='{self._registry}'"

    def get_workers(self, node: "TestNode" = None) -> set[str]:
        """
        Get all worker visits for the given (possibly bridged) test node are all nodes.

        :param node: possibly registered test node to get visits for
        :returns: all visits by all workers as worker references (allowing repetitions)
        """
        worker_keys = set()
        node_keys = [node.bridged_form] if node else self._registry.keys()
        for node_key in node_keys:
            worker_keys |= {*self._registry.get(node_key, {}).keys()}
        return worker_keys

    def get_counters(self, node: "TestNode" = None, worker: TestWorker = None) -> int:
        """
        Get all workers in the current register.

        :param node: optional test node to get counters for
        :param worker: optional worker to get counters for
        :returns: counter for a given node or worker (typically both)
        """
        counter = 0
        node_keys = [node.bridged_form] if node else self._registry.keys()
        for node_key in node_keys:
            worker_keys = [worker.id] if worker else self._registry.get(node_key, {}).keys()
            for worker_key in worker_keys:
                counter += self._registry.get(node_key, {}).get(worker_key, 0)
        return counter

    def register(self, node: "TestNode", worker: TestWorker) -> None:
        """
        Register a worker visit for the given (possibly bridged) test node.

        :param node: possibly registered test node to register visits for
        :param worker: worker that visited the test node
        """
        if node.bridged_form not in self._registry:
            self._registry[node.bridged_form] = {}
        if worker.id not in self._registry[node.bridged_form]:
            self._registry[node.bridged_form][worker.id] = 0
        self._registry[node.bridged_form][worker.id] += 1


class TestNode(Runnable):
    """
    A wrapper for all test relevant parts like parameters, parser, used
    objects and dependencies to/from other test nodes (setup/cleanup).
    """

    def params(self):
        """Parameters (cache) property."""
        if self._params_cache is None:
            self.regenerate_params()
        return self._params_cache
    params = property(fget=params)

    def shared_workers(self):
        """Workers that have previously finished traversing this node (incl. leaves and others)."""
        workers = {*self.workers}
        for bridged_node in self._bridged_nodes:
            workers |= bridged_node.workers
        return workers
    shared_workers = property(fget=shared_workers)

    def shared_results(self):
        """Test results shared across all bridged nodes."""
        results = list(self.results)
        for bridged_node in self._bridged_nodes:
            results += bridged_node.results
        return results
    shared_results = property(fget=shared_results)

    def final_restr(self):
        """Final restriction to make the object parsing variant unique."""
        return self.recipe.steps[-2].parsable_form()
    final_restr = property(fget=final_restr)

    def setless_form(self):
        """Test set invariant form of the test node name."""
        max_restr = ""
        for main_restr in self.params.objects("main_restrictions"):
            if self.params["name"].startswith(main_restr):
                max_restr = main_restr if len(main_restr) > len(max_restr) else max_restr
        return self.params["name"].replace(max_restr + ".", "", 1)
    setless_form = property(fget=setless_form)

    def bridged_form(self):
        """Test worker invariant form of the test node name."""
        # TODO: the order of parsing nets and vms has to be improved
        if len(self.objects) == 0:
            return self.setless_form
        # TODO: the long suffix does not contain anything reasonable
        #suffix = self.objects[0].long_suffix
        suffix = self.params["_name_map_file"].get("nets.cfg", "")
        # since this doesn't use the prefix tree a regex could match part of a variant
        return  "\." + self.setless_form.replace(suffix, ".+") + "$"
    bridged_form = property(fget=bridged_form)

    def long_prefix(self):
        """Sufficiently unique prefix to identify a diagram test node."""
        return self.prefix + "-" + self.params["vms"].replace(" ", "")
    long_prefix = property(fget=long_prefix)

    def id(self):
        """Unique ID to identify a test node."""
        return self.long_prefix + "-" + self.params["name"]
    id = property(fget=id)

    def id_test(self):
        """Unique test ID to identify a test node."""
        # TODO: cannot reuse long prefix since container is set at runtime
        #return TestID(self.long_prefix, self.params["name"])
        net_id = self.params.get("nets_gateway", "")
        net_id += "." if net_id else ""
        net_id += self.params.get("nets_host", "")
        net_id += self.params["vms"].replace(" ", "")
        full_prefix = self.prefix + "-" + net_id
        return TestID(full_prefix, self.params["name"])
    id_test = property(fget=id_test)

    _session_cache = {}

    def __init__(self, prefix, recipe):
        """
        Construct a test node (test) for any test objects (vms).

        :param str name: name of the test node
        :param recipe: variant parsing recipe for the test node
        :type recipe: :py:class:`param.Reparsable`
        """
        super().__init__("avocado-vt", prefix, {})

        self.prefix = prefix
        self.recipe = recipe
        self._params_cache = None

        self.should_run = self.default_run_decision
        self.should_clean = self.default_clean_decision

        self.workers = set()
        self.worker = None
        self.results = []
        self._bridged_nodes = []

        self.objects = []

        # lists of parent and children test nodes
        self.setup_nodes = []
        self.cleanup_nodes = []
        self._picked_by_setup_nodes = EdgeRegister()
        self._picked_by_cleanup_nodes = EdgeRegister()
        self._dropped_setup_nodes = EdgeRegister()
        self._dropped_cleanup_nodes = EdgeRegister()

    def __repr__(self):
        shortname = self.params.get("shortname", "<unknown>")
        return f"[node] longprefix='{self.long_prefix}', shortname='{shortname}'"

    def set_environment(self, worker: TestWorker) -> None:
        """
        Set the environment for executing the test node.

        :param worker: set an optional worker or run serially if none given
                       for unisolated process spawners

        This isolating environment could be a container, a virtual machine, or
        a less-isolated process and is managed by a specialized spawner.
        """
        self.params["nets_gateway"] = worker.params["nets_gateway"]
        self.params["nets_host"] = worker.params["nets_host"]
        self.params["nets_spawner"] = worker.params["nets_spawner"]
        self.worker = worker

    def set_objects_from_net(self, net: NetObject) -> None:
        """
        Set all node's objects from a provided test net.

        :param net: test net to use as first and top object
        """
        # flattened list of objects (in composition) involved in the test
        self.objects = [net]
        # TODO: only three nesting levels from a test net are supported
        for test_object in net.components:
            self.objects += [test_object]
            self.objects += test_object.components
            # TODO: dynamically added additional images will not be detected here
            from . import ImageObject
            from .. import params_parser as param
            vm_name = test_object.suffix
            parsed_images = [c.suffix for c in test_object.components]
            for image_name in self.params.object_params(vm_name).objects("images"):
                if image_name not in parsed_images:
                    image_suffix = f"{image_name}_{vm_name}"
                    config = param.Reparsable()
                    config.parse_next_dict(test_object.params.object_params(image_name))
                    config.parse_next_dict({"object_suffix": image_suffix, "object_type": "images"})
                    image = ImageObject(image_suffix, config)
                    image.composites.append(test_object)
                    self.objects += [image]

    def is_occupied(self):
        for bridged_node in self._bridged_nodes:
            if bridged_node.worker is not None:
                return True
        return self.worker is not None

    def is_flat(self):
        """Check if the test node is flat and does not yet have objects and dependencies to evaluate."""
        return len(self.objects) == 0

    def is_scan_node(self):
        """Check if the test node is the root of all test nodes for all test objects."""
        return self.prefix.endswith("0s1")

    def is_terminal_node(self):
        """Check if the test node is the root of all test nodes for some test object."""
        return self.prefix.endswith("t")

    def is_shared_root(self):
        """Check if the test node is the root of all test nodes for all test objects."""
        return self.params.get_boolean("shared_root", False)

    def is_object_root(self):
        """Check if the test node is the root of all test nodes for some test object."""
        return "object_root" in self.params

    def is_objectless(self):
        """Check if the test node is not defined with any test object."""
        return len(self.objects) == 0 or self.params["vms"] == ""

    def is_unrolled(self, worker: TestWorker) -> bool:
        """
        Check if the test is unrolled as composite node with dependencies.

        :param worker: worker a flat node is unrolled for
        :raises: :py:class:`RuntimeError` if the current node is not flat (cannot be unrolled)
        """
        if not self.is_flat():
            raise RuntimeError(f"Only flat nodes can be unrolled, {self} is not flat")
        for node in self.cleanup_nodes:
            if self.params["name"] in node.id and worker.id in node.id:
                return True
        return False

    def is_setup_ready(self, worker: TestWorker) -> bool:
        """
        Check if all dependencies of the test were run or there were none.

        :param worker: relative setup readiness with respect to a worker ID
        """
        for node in self.setup_nodes:
            if worker.id not in self._dropped_setup_nodes.get_workers(node):
                return False
        return True

    def is_cleanup_ready(self, worker: TestWorker) -> bool:
        """
        Check if all dependent tests were run or there were none.

        :param str worker: relative setup readiness with respect to a worker ID
        """
        for node in self.cleanup_nodes:
            if worker.id not in self._dropped_cleanup_nodes.get_workers(node):
                return False
        return True

    def is_eagerly_finished(self, worker: TestWorker = None) -> bool:
        """
        The test was run by at least one worker of all or some scopes.

        :param worker: evaluate with respect to an optional worker ID scope or globally if none given
        :returns: whether the test was run by at least one worker of all or some scopes

        This happens in an eager manner so that any already available
        setup nodes are considered finished. If we instead wait for
        this setup to be cleaned up or synced, this would count most
        of the setup as finished in the very end of the traversal.
        """
        if worker and "swarm" not in self.params["pool_scope"] and self.params.get("nets_spawner") == "lxc":
            # is finished separately by each worker
            return worker in self.shared_workers
        elif worker and "cluster" not in self.params["pool_scope"] and self.params.get("nets_spawner") == "remote":
            # is finished for an entire swarm by at least one of its workers
            return worker.params["runtime_str"].split("/")[0] in set(worker.params["runtime_str"].split("/")[0] for worker in self.shared_workers)
        else:
            # is finished globally by at least one worker
            return len(self.shared_workers) > 0

    def is_fully_finished(self, worker: TestWorker = None) -> bool:
        """
        The test was run by all workers of a given scope.

        :param worker: evaluate with respect to an optional worker ID scope or globally if none given
        :returns: whether the test was run all workers of a given scope

        The consideration here is for fully traversed node by all workers
        unless restricted within some scope of setup reuse.
        """
        if worker and "swarm" not in self.params["pool_scope"] and self.params.get("nets_spawner") == "lxc":
            # is finished separately by each worker and for all workers
            return worker in self.shared_workers
        elif worker and "cluster" not in self.params["pool_scope"] and self.params.get("nets_spawner") == "remote":
            # is finished for an entire swarm by all of its workers
            slot_cluster = worker.params["runtime_str"].split("/")[0]
            all_cluster_hosts = set(host for host in TestWorker.run_slots[slot_cluster])
            node_cluster_hosts = set(worker.params["runtime_str"].split("/")[1] for worker in self.shared_workers if worker.params["runtime_str"].split("/")[0] == slot_cluster)
            return all_cluster_hosts == node_cluster_hosts
        else:
            # is finished globally by all workers
            return len(self.shared_workers) == sum([len([w for w in TestWorker.run_slots[s]]) for s in TestWorker.run_slots])

    def is_terminal_node_for(self):
        """
        Determine any object that this node is a root of.

        :returns: object that this node is a root of if any
        :rtype: :py:class:`TestObject` or None
        """
        object_root = self.params.get("object_root")
        if not object_root:
            return object_root
        for test_object in self.objects:
            if test_object.id == object_root:
                return test_object

    def produces_setup(self):
        """
        Check if the test node produces any reusable setup state.

        :returns: whether there are setup states to reuse from the test
        :rtype: bool
        """
        for test_object in self.objects:
            object_params = test_object.object_typed_params(self.params)
            object_state = object_params.get("set_state")
            if object_state:
                return True
        return False

    def has_dependency(self, state, test_object):
        """
        Check if the test node has a dependency parsed and available.

        :param str state: name of the dependency (state or parent test set)
        :param test_object: object used for the dependency
        :type test_object: :py:class:`TestObject`
        :returns: whether the dependency was already found among the setup nodes
        :rtype: bool
        """
        for test_node in self.setup_nodes:
            # TODO: direct object compairson will not work for dynamically
            # (within node) created objects like secondary images
            node_object_suffices = [t.long_suffix for t in test_node.objects]
            if test_object in test_node.objects or test_object.long_suffix in node_object_suffices:
                if re.search("(\.|^)" + state + "(\.|$)", test_node.params.get("name")):
                    return True
                setup_object_params = test_object.object_typed_params(test_node.params)
                if state == setup_object_params.get("set_state"):
                    return True
        return False

    def should_rerun(self, worker: TestWorker = None) -> bool:
        """
        Check if the test node should be rerun based on some retry criteria.

        :param worker: evaluate with respect to an optional worker ID scope or globally if none given
        :returns: whether the test node should be retried

        The retry parameters are `max_tries` and `rerun_status` or `stop_status`. The
        first is the maximum number of tries, and the second two indicate when to continue
        or stop retrying in terms of encountered test status and can be a list of statuses.
        """
        if self.params.get("dry_run", "no") == "yes":
            logging.info(f"Should not rerun via dry test run {self}")
            return False
        elif self.is_flat():
            logging.debug(f"Should not rerun a flat node {self}")
            return False
        elif self.is_shared_root():
            logging.debug(f"Should not rerun the shared root")
            return False
        elif worker and worker.id not in self.params["name"]:
            raise RuntimeError(f"Worker {worker.id} should not consider rerunning {self}")

        all_statuses = ["fail", "error", "pass", "warn", "skip", "cancel", "interrupted"]
        if self.params.get("replay"):
            rerun_status = self.params.get_list("rerun_status", "fail,error,warn", delimiter=",")
        else:
            rerun_status = self.params.get_list("rerun_status", []) or all_statuses
        stop_status = self.params.get_list("stop_status", [])
        for status, status_type in [(rerun_status, "rerun"), (stop_status, "stop")]:
            disallowed_status = {*status} - {*all_statuses}
            if len(disallowed_status) > 0:
                raise ValueError(f"Value of {status_type} status must be a valid test status,"
                                 f" found {', '.join(disallowed_status)}")

        # ignore the retry parameters for nodes that cannot be re-run (need to run at least once)
        max_tries = self.params.get_numeric("max_tries", 2 if self.params.get("replay") else 1)
        # do not log when the user is not using the retry feature
        if max_tries > 1:
            stop_condition = ", ".join(stop_status) if stop_status else "NONE"
            rerun_condition = ", ".join(rerun_status) if rerun_status else "NONE"
            logging.debug(f"Could rerun {self} with stop condition {stop_condition}, a rerun condition "
                          f"{rerun_condition}, and a maximum of {max_tries} tries")
        if max_tries < 0:
           raise ValueError("Number of max_tries cannot be less than zero")

        # analyzing rerun and stop status conditions
        test_statuses = [r["status"].lower() for r in self.shared_results]
        rerun_statuses_violated = {*test_statuses} - {*rerun_status}
        if len(rerun_statuses_violated) > 0:
            logging.debug(f"Stopping test tries due to violated rerun test statuses: {rerun_status}")
            return False
        stop_statuses_found = {*stop_status} & {*test_statuses}
        if len(stop_statuses_found) > 0:
            logging.info(f"Stopping test tries due to obtained stop test statuses: {', '.join(stop_statuses_found)}")
            return False

        # implicitly this means that setting >1 retries will be done on tests actually collecting results (no flat nodes, dry runs, etc.)
        reruns_left = 0 if max_tries == 1 else max_tries - len(test_statuses)
        if reruns_left > 0:
            logging.debug(f"Still have {reruns_left} allowed reruns left and should rerun {self}")
            return True
        logging.debug(f"Should not rerun {self}")
        return False

    def should_scan(self, worker: TestWorker = None) -> bool:
        """
        Check if the test node should scan for reusable states based on scope criteria.

        :param worker: evaluate with respect to an optional worker ID scope or globally if none given
        :returns: whether the test node should scan for states
        """
        if worker and "swarm" not in self.params["pool_scope"] and self.params.get("nets_spawner") == "lxc":
            # is scanned separately by each worker
            return worker not in self.shared_workers
        elif worker and "cluster" not in self.params["pool_scope"] and self.params.get("nets_spawner") == "remote":
            # is scanned for an entire swarm by just one of its workers
            return worker.params["runtime_str"].split("/")[0] not in set(worker.params["runtime_str"].split("/")[0] for worker in self.shared_workers)
        else:
            # should scan globally by just one worker
            return len(self.shared_workers) == 0

    def default_run_decision(self, worker: TestWorker) -> bool:
        """
        Default decision policy on whether a test node should be run or skipped.

        :param worker: worker which makes the run decision
        :returns: whether the worker should run the test node
        """
        if not self.is_flat() and worker.id not in self.params["name"]:
            raise RuntimeError(f"Worker {worker.id} should not try to run {self}")

        if not self.produces_setup():
            # most standard stateless behavior is to run each test node once then rerun if needed
            should_run = len(self.shared_results) == 0 or self.should_rerun(worker)

        else:
            should_run_from_scan = False
            should_scan = self.should_scan(worker)
            if should_scan:
                should_run_from_scan = self.scan_states()
                logging.debug(f"Should{' ' if should_run_from_scan else ' not '}run from scan {self}")
            # rerunning of test from previous jobs is never intended
            if len(self.shared_results) == 0 and not should_run_from_scan:
                self.should_rerun = lambda _: False

            should_run = should_run_from_scan if should_scan else False
            should_run = should_run or self.should_rerun(worker)

        return should_run

    def default_clean_decision(self, worker: TestWorker) -> bool:
        """
        Default decision policy on whether a test node should be cleaned or skipped.

        :param worker: worker which makes the clean decision
        :returns: whether the worker should clean the test node
        """
        if not self.is_flat() and worker.id not in self.params["name"]:
            raise RuntimeError(f"Worker {worker.id} should not try to clean {self}")

        # no support for parallelism within reversible nodes since we might hit a race condition
        # whereby a node will be run for missing setup but its parent will be reversed before it
        # gets any parent-provided states
        is_reversible = True
        for test_object in self.objects:
            object_params = test_object.object_typed_params(self.params)
            is_reversible = object_params.get("unset_mode_images", object_params["unset_mode"])[0] == "f"
            is_reversible |= object_params.get("unset_mode_vms", object_params["unset_mode"])[0] == "f"
            if is_reversible:
                break

        if not is_reversible:
            return True
        else:
            # last one of a given scope should "close the door" for that scope
            return self.is_fully_finished(worker)

    @classmethod
    def prefix_priority(cls, prefix1, prefix2):
        """
        Class method for secondary prioritization using test prefixes.

        :param str prefix1: first prefix to use for the priority comparison
        :param str prefix2: second prefix to use for the priority comparison
        :returns: -1 if prefix1 < prefix2, 1 if prefix1 > prefix2, 0 otherwise

        This function also does recursive calls of sub-prefixes.
        """
        if prefix1 == prefix2:
            # identical prefixes detected, nothing we can do but choose a default
            return 1
        match1, match2 = re.match(r"^(\d+)(\w)(.+)", prefix1), re.match(r"^(\d+)(\w)(.+)", prefix2)
        digit1, alpha1, else1 = (prefix1, None, None) if match1 is None else match1.group(1, 2, 3)
        digit2, alpha2, else2 = (prefix2, None, None) if match2 is None else match2.group(1, 2, 3)

        # compare order of parsing if simple leaf nodes
        if digit1.isdigit() and digit2.isdigit():
            digit1, digit2 = int(digit1), int(digit2)
            if digit1 != digit2:
                return digit1 - digit2
        # we no longer match and are at the end of the prefix
        else:
            if digit1 != digit2:
                return 1 if digit1 > digit2 else -1

        # compare the node type flags next
        if alpha1 != alpha2:
            if alpha1 is None:
                return 1 if alpha2 == "a" else -1  # reverse order for "c" (cleanup), "b" (byproduct), "d" (duplicate)
            if alpha2 is None:
                return -1 if alpha1 == "a" else 1  # reverse order for "c" (cleanup), "b" (byproduct), "d" (duplicate)
            return 1 if alpha1 > alpha2 else -1
        # redo the comparison for the next prefix part
        else:
            assert else1 is not None, f"could not match test prefix part {prefix1} to choose priority"
            assert else2 is not None, f"could not match test prefix part {prefix2} to choose priority"
            return cls.prefix_priority(else1, else2)

    def pick_parent(self, worker: TestWorker) -> "TestNode":
        """
        Pick the next available parent based on some priority.

        :param worker: worker for which the parent is selected
        :returns: the next parent node
        :raises: :py:class:`RuntimeError`

        The current order will prioritize less traversed test paths.
        """
        available_nodes = [n for n in self.setup_nodes if worker.id in n.params["name"] or n.is_flat() or n.is_shared_root()]
        available_nodes = [n for n in available_nodes if worker.id not in self._dropped_setup_nodes.get_workers(n)]
        if len(available_nodes) == 0:
            raise RuntimeError(f"Picked a parent of a node without remaining parents for {self}")
        sorted_nodes = sorted(available_nodes, key=cmp_to_key(lambda x, y: TestNode.prefix_priority(x.long_prefix, y.long_prefix)))
        sorted_nodes = sorted(sorted_nodes, key=lambda n: n._picked_by_cleanup_nodes.get_counters())
        sorted_nodes = sorted(sorted_nodes, key=lambda n: int(not n.is_flat()))

        test_node = sorted_nodes[0]
        test_node._picked_by_cleanup_nodes.register(self, worker)
        return test_node

    def pick_child(self, worker: TestWorker) -> "TestNode":
        """
        Pick the next available child based on some priority.

        :param worker: worker for which the child is selected
        :returns: the next child node
        :raises: :py:class:`RuntimeError`

        The current order will prioritize less traversed test paths.
        """
        available_nodes = [n for n in self.cleanup_nodes if worker.id in n.params["name"] or n.is_flat() or n.is_shared_root()]
        available_nodes = [n for n in available_nodes if worker.id not in self._dropped_cleanup_nodes.get_workers(n)]
        if len(available_nodes) == 0:
            raise RuntimeError(f"Picked a child of a node without remaining children for {self}")
        sorted_nodes = sorted(available_nodes, key=cmp_to_key(lambda x, y: TestNode.prefix_priority(x.long_prefix, y.long_prefix)))
        sorted_nodes = sorted(sorted_nodes, key=lambda n: n._picked_by_setup_nodes.get_counters())
        sorted_nodes = sorted(sorted_nodes, key=lambda n: int(not n.is_flat()))

        test_node = sorted_nodes[0]
        test_node._picked_by_setup_nodes.register(self, worker)
        return test_node

    def drop_parent(self, test_node: "TestNode", worker: TestWorker) -> None:
        """
        Add a parent node to the set of visited nodes for this test.

        :param test_node: visited node
        :param worker: worker visiting the node
        :raises: :py:class:`ValueError` if visited node is not directly dependent
        """
        if test_node not in self.setup_nodes:
            raise ValueError(f"Invalid parent to drop: {test_node} not a parent of {self}")
        self._dropped_setup_nodes.register(test_node, worker)

    def drop_child(self, test_node: "TestNode", worker: TestWorker) -> None:
        """
        Add a child node to the set of visited nodes for this test.

        :param test_node: visited node
        :param worker: worker visiting the node
        :raises: :py:class:`ValueError` if visited node is not directly dependent
        """
        if test_node not in self.cleanup_nodes:
            raise ValueError(f"Invalid child to drop: {test_node} not a child of {self}")
        self._dropped_cleanup_nodes.register(test_node, worker)

    def bridge_node(self, test_node: "TestNode") -> None:
        """
        Bridge current node with equivalent node for a different worker.

        :param test_node: equivalent node for a different worker
        :raises: :py:class:`ValueError` if bridged node is not equivalent
        """
        if test_node == self:
            return
        # TODO: cannot do simpler comparison due to current limitations in the bridged form
        elif not re.search(test_node.bridged_form, self.params["name"]):
            raise ValueError(f"Cannot bridge {self} with non-equivalent {test_node}")
        if test_node not in self._bridged_nodes:
            logging.info(f"Bridging {self.params['shortname']} to {test_node.params['shortname']}")
            self._bridged_nodes.append(test_node)
            test_node._bridged_nodes.append(self)

            self._picked_by_setup_nodes = test_node._picked_by_setup_nodes
            self._dropped_setup_nodes = test_node._dropped_setup_nodes
            self._picked_by_cleanup_nodes = test_node._picked_by_cleanup_nodes
            self._dropped_cleanup_nodes = test_node._dropped_cleanup_nodes

    def add_location(self, location: str) -> None:
        """
        Add a setup reuse location information to the current node.

        :param str location: a special format string containing all information on the
                             location where the format must be "gateway/host:path"
        """
        def ip_and_port_from_location(location):
            location_tuple = location.split(":")
            gateway, host = ("", "") if len(location_tuple) <= 1 else location_tuple[0].split("/")
            ip, port = NetObject.get_session_ip_port(host, gateway,
                                                    self.params['nets_ip_prefix'],
                                                    self.params["nets_shell_port"])
            return ip, port

        if self.params.get("set_location"):
            if location not in self.params["set_location"]:
                self.params["set_location"] += " " + location
        else:
            self.params["set_location"] = location
        source_suffix = "_" + location
        ip, port = ip_and_port_from_location(location)
        self.params[f"nets_shell_host{source_suffix}"] = ip
        self.params[f"nets_shell_port{source_suffix}"] = port
        self.params[f"nets_file_transfer_port{source_suffix}"] = port

        for node in self.setup_nodes:
            # TODO: networks need further refactoring possibly as node environments
            object_suffix = node.params.get("object_suffix", "net1")
            # discard parameters if we are not talking about any specific non-net object
            object_suffix = "_" + object_suffix if object_suffix != "net1" else "_none"
            setup_locations = node.params.get_list("set_location", [])

            if self.params.get(f"get_location{object_suffix}"):
                for setup_location in setup_locations:
                    if setup_location not in self.params[f"get_location{object_suffix}"]:
                        self.params[f"get_location{object_suffix}"] += " " + setup_location
            else:
                self.params[f"get_location{object_suffix}"] = " ".join(setup_locations)

            for setup_location in setup_locations:
                source_suffix = "_" + setup_location
                source_object_suffix = source_suffix + object_suffix
                ip, port = ip_and_port_from_location(setup_location)
                self.params[f"nets_shell_host{source_object_suffix}"] = ip
                self.params[f"nets_shell_port{source_object_suffix}"] = port
                self.params[f"nets_file_transfer_port{source_object_suffix}"] = port

    def regenerate_params(self, verbose=False):
        """
        Regenerate all parameters from the current reparsable config.

        :param bool verbose: whether to show generated parameter dictionaries
        """
        self._params_cache = self.recipe.get_params(show_dictionaries=verbose)
        self.regenerate_vt_parameters()

    def regenerate_vt_parameters(self):
        """
        Regenerate the parameters provided to the VT runner.
        """
        uri = self.params.get('name')
        vt_params = self.params.copy()
        # Flatten the vt_params, discarding the attributes that are not
        # scalars, and will not be used in the context of nrunner
        for key in ('_name_map_file', '_short_name_map_file', 'dep'):
            if key in self.params:
                del(vt_params[key])
        super().__init__('avocado-vt', uri, **vt_params)

    def get_session_ip_port(self):
        """
        Get an IP address and a port to the current slot for the given test node.

        :returns: IP and port in string parameter format
        :rtype: (str, str)
        """
        return NetObject.get_session_ip_port(self.params['nets_host'],
                                             self.params['nets_gateway'],
                                             self.params['nets_ip_prefix'],
                                             self.params["nets_shell_port"])

    def get_session_to_net(self):
        """
        Get a remote session to the current slot for the given test node.

        :returns: remote session to the slot determined from current node environment
        :rtype: :type session: :py:class:`aexpect.ShellSession`
        """
        log.getLogger("aexpect").parent = log.getLogger("avocado.job")
        host, port = self.get_session_ip_port()
        address = host + ":" + port
        cache = type(self)._session_cache
        session = cache.get(address)
        if session:
            # check for corrupted sessions
            try:
                logging.debug("Remote session health check: " + session.cmd_output("date"))
            except ShellTimeoutError as error:
                logging.warning(f"Bad remote session health for {address}!")
                session = None
        if not session:
            session = remote.wait_for_login(self.params["nets_shell_client"],
                                            host, port,
                                            self.params["nets_username"], self.params["nets_password"],
                                            self.params["nets_shell_prompt"])
            cache[address] = session

        return session

    def scan_states(self):
        """
        Scan for present object states to reuse the test from previous runs.

        :returns: whether all required states are available
        :rtype: bool
        """
        should_run = True
        node_params = self.params.copy()

        slot, slothost = self.params["nets_host"], self.params["nets_gateway"]
        is_leaf = True
        for test_object in self.objects:
            object_params = test_object.object_typed_params(self.params)
            object_state = object_params.get("set_state")

            # the test leaves an object undefined so it cannot be reused for this object
            if object_state is None or object_state == "":
                continue
            else:
                is_leaf = False

            # the object state has to be defined to reach this stage
            if object_state == "install" and test_object.is_permanent():
                should_run = False
                break

            # ultimate consideration of whether the state is actually present
            object_suffix = f"_{test_object.key}_{test_object.long_suffix}"
            node_params[f"check_state{object_suffix}"] = object_state
            node_params[f"show_location{object_suffix}"] = object_params["set_location"]
            node_params[f"check_mode{object_suffix}"] = object_params.get("check_mode", "rf")
            # TODO: unfortunately we need env object with pre-processed vms in order
            # to provide ad-hoc root vm states so we use the current advantage that
            # all vm state backends can check for states without a vm boot (root)
            if test_object.key == "vms":
                node_params[f"use_env{object_suffix}"] = "no"
            node_params[f"soft_boot{object_suffix}"] = "no"

        if not is_leaf:
            session = self.get_session_to_net()
            control_path = os.path.join(self.params["suite_path"], "controls", "pre_state.control")
            mod_control_path = door.set_subcontrol_parameter(control_path, "action", "check")
            mod_control_path = door.set_subcontrol_parameter_dict(mod_control_path, "params", node_params)
            try:
                door.run_subcontrol(session, mod_control_path)
                should_run = False
            except ShellCmdError as error:
                if "AssertionError" in error.output:
                    should_run = True
                else:
                    raise RuntimeError("Could not complete state scan due to control file error")
        logging.info(f"The test node {self} %s run from a scan on {slothost + '/' + slot}",
                     "should" if should_run else "should not")
        return should_run

    def sync_states(self, params):
        """Sync or drop present object states to clean or later skip tests from previous runs."""
        node_params = self.params.copy()
        for key in list(node_params.keys()):
            if key.startswith("get_state") or key.startswith("unset_state"):
                del node_params[key]

        # the sync cleanup will be performed if at least one selected object has a cleanable state
        slot, slothost = self.params["nets_host"], self.params["nets_gateway"]
        should_clean = False
        for test_object in self.objects:
            object_params = test_object.object_typed_params(self.params)
            object_state = object_params.get("set_state")
            if not object_state:
                continue

            # avoid running any test unless the user really requires cleanup or setup is reusable
            unset_policy = object_params.get("unset_mode", "ri")
            if unset_policy[0] not in ["f", "r"]:
                continue
            # avoid running any test for unselected vms
            if test_object.key == "nets":
                logging.warning("Net state cleanup is not supported")
                continue
            # the object state has to be defined to reach this stage
            if object_state == "install" and test_object.is_permanent():
                should_clean = False
                break
            vm_name = test_object.suffix if test_object.key == "vms" else test_object.composites[0].suffix
            # TODO: is this needed?
            from .. import params_parser as param
            if vm_name in params.get("vms", param.all_objects("vms")):
                should_clean = True
            else:
                continue

            # TODO: cannot remove ad-hoc root states, is this even needed?
            if test_object.key == "vms":
                vm_params = object_params
                node_params["images_" + vm_name] = vm_params["images"]
                for image_name in vm_params.objects("images"):
                    image_params = vm_params.object_params(image_name)
                    node_params[f"image_name_{image_name}_{vm_name}"] = image_params["image_name"]
                    node_params[f"image_format_{image_name}_{vm_name}"] = image_params["image_format"]
                    if image_params.get_boolean("create_image", False):
                        node_params[f"remove_image_{image_name}_{vm_name}"] = "yes"
                        node_params["skip_image_processing"] = "no"

            suffixes = f"_{test_object.key}_{test_object.suffix}"
            suffixes += f"_{vm_name}" if test_object.key == "images" else ""
            # spread the state setup for the given test object
            location = object_params["set_location"]
            if unset_policy[0] == "f":
                # reverse the state setup for the given test object
                # NOTE: we are forcing the unset_mode to be the one defined for the test node because
                # the unset manual step behaves differently now (all this extra complexity starts from
                # the fact that it has different default value which is noninvasive
                node_params.update({f"unset_state{suffixes}": object_state,
                                    f"unset_location{suffixes}": location,
                                    f"unset_mode{suffixes}": object_params.get("unset_mode", "ri"),
                                    f"pool_scope": "own"})
                do = "unset"
                logging.info(f"Need to clean up {self} on {slot}")
            else:
                # spread the state setup for the given test object
                node_params.update({f"get_state{suffixes}": object_state,
                                    f"get_location{suffixes}": location})
                node_params[f"pool_scope{suffixes}"] = object_params.get("pool_scope", "swarm cluster shared")
                # NOTE: "own" may not be removed because we skip "own" scope here which is done for both
                # speed and the fact that it is not equivalent to reflexive download (actually getting a state)
                for sync_source in location.split():
                    if sync_source.startswith(slothost + '/' + slot):
                        logging.info(f"No need to sync {self} from {slot} to itself")
                        should_clean = False
                        break
                else:
                    logging.info(f"Need to sync {self} from {location.join(',')} to {slot}")
                do = "get"
            # TODO: unfortunately we need env object with pre-processed vms in order
            # to provide ad-hoc root vm states so we use the current advantage that
            # all vm state backends can check for states without a vm boot (root)
            if test_object.key == "vms":
                node_params[f"use_env_{test_object.key}_{test_object.suffix}"] = "no"

        if should_clean:
            action = "Cleaning up" if unset_policy[0] == "f" else "Syncing"
            logging.info(f"{action} {self} on {slot}")
            session = self.get_session_to_net()
            control_path = os.path.join(self.params["suite_path"], "controls", "pre_state.control")
            mod_control_path = door.set_subcontrol_parameter(control_path, "action", do)
            mod_control_path = door.set_subcontrol_parameter_dict(mod_control_path, "params", node_params)
            try:
                door.run_subcontrol(session, mod_control_path)
            except ShellCmdError as error:
                logging.warning(f"{action} {self} on {slot} could not be completed "
                                f"due to control file error: {error}")
        else:
            logging.info(f"No need to clean up or sync {self} on {slot}")

    def validate(self):
        """Validate the test node for sane attribute-parameter correspondence."""
        param_nets = self.params.objects("nets")
        attr_nets = list(o.suffix for o in self.objects if o.key == "nets")
        if len(attr_nets) > 1 or len(param_nets) > 1:
            raise AssertionError(f"Test node {self} can have only one net ({attr_nets}/{param_nets}")
        param_net_name, attr_net_name = attr_nets[0], param_nets[0]
        if self.objects and self.objects[0].suffix != attr_net_name:
            raise AssertionError(f"The net {attr_net_name} must be the first node object {self.objects[0]}")
        if param_net_name != attr_net_name:
            raise AssertionError(f"Parametric and attribute nets differ {param_net_name} != {attr_net_name}")

        param_vms = set(self.params.objects("vms"))
        attr_vms = set(o.suffix for o in self.objects if o.key == "vms")
        if len(param_vms - attr_vms) > 0:
            raise ValueError("Additional parametric objects %s not in %s" % (param_vms, attr_vms))
        if len(attr_vms - param_vms) > 0:
            raise ValueError("Missing parametric objects %s from %s" % (param_vms, attr_vms))

        # TODO: images can currently be ad-hoc during run and thus cannot be validated

        if self in self.setup_nodes or self in self.cleanup_nodes:
            raise ValueError("Detected reflexive dependency of %s to itself" % self)
