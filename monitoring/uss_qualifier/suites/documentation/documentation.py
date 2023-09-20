from __future__ import annotations
import glob
import inspect
import os
from dataclasses import dataclass
from typing import Iterator, Optional, List, Union, Dict

from implicitdict import ImplicitDict
from monitoring.uss_qualifier.action_generators.action_generator import (
    action_generator_type_from_name,
)
from monitoring.uss_qualifier.action_generators.definitions import (
    ActionGeneratorDefinition,
)
from monitoring.uss_qualifier.action_generators.documentation.definitions import (
    PotentialGeneratedAction,
    PotentialActionGeneratorAction,
)
from monitoring.uss_qualifier.action_generators.documentation.documentation import (
    list_potential_actions_for_action_generator_definition,
)

from monitoring.uss_qualifier.fileio import (
    load_dict_with_references,
    get_package_name,
    resolve_filename,
    FileReference,
)
from monitoring.uss_qualifier.requirements.definitions import RequirementID
from monitoring.uss_qualifier.scenarios.definitions import TestScenarioTypeName
from monitoring.uss_qualifier.scenarios.documentation.definitions import (
    TestScenarioDocumentation,
    TestCheckDocumentation,
)
from monitoring.uss_qualifier.scenarios.documentation.parsing import (
    get_documentation,
    get_documentation_by_name,
)
from monitoring.uss_qualifier.scenarios.scenario import get_scenario_type_by_name
from monitoring.uss_qualifier.suites import suite as suite_module
from monitoring.uss_qualifier.suites.definitions import (
    TestSuiteDefinition,
    ActionType,
    TestSuiteActionDeclaration,
)


@dataclass
class TestSuiteRenderContext(object):
    parent_yaml_file: str
    parent_doc_file: str
    base_path: str
    list_index: int
    indent: int
    test_suites: Dict[str, str]


def find_test_suites(start_path: Optional[str] = None) -> Iterator[str]:
    if start_path is None:
        start_path = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    for yaml in glob.glob(os.path.join(start_path, "*.yaml")):
        yield yaml
    for subfolder in os.listdir(start_path):
        full_path = os.path.join(start_path, subfolder)
        if not os.path.isdir(full_path):
            continue
        for suite in find_test_suites(full_path):
            yield suite


def make_test_suite_documentation(
    suite_def: TestSuiteDefinition,
    suite_yaml_file: str,
    suite_doc_file: str,
    parent_suite_doc: Optional[str] = None,
) -> Dict[str, str]:
    test_suites: Dict[str, str] = {}

    lines = []
    lines.append(
        "<!--This file is autogenerated via `make format`; do not change manually-->"
    )
    lines.append(f"# {suite_def.name} test suite")
    local_path = os.path.split(suite_yaml_file)[-1]
    if parent_suite_doc is None:
        prefix = ""
    else:
        parent_rel_path = os.path.relpath(
            parent_suite_doc, start=os.path.dirname(suite_doc_file)
        )
        prefix = f"Defined in [parent suite]({parent_rel_path}) "
    lines.append(f"{prefix}[`{get_package_name(suite_yaml_file)}`](./{local_path})")
    lines.append("")

    suite_readme_abspath = os.path.join(
        os.path.dirname(suite_module.__file__), "README.md"
    )
    base_path = os.path.dirname(suite_yaml_file)
    suites_readme_path = os.path.relpath(suite_readme_abspath, start=base_path)
    lines.append(f"## [Actions]({suites_readme_path}#actions)")
    lines.append("")
    i = 0
    for i, action in enumerate(suite_def.actions):
        lines.extend(
            _render_action(
                action,
                TestSuiteRenderContext(
                    parent_yaml_file=suite_yaml_file,
                    parent_doc_file=suite_doc_file,
                    base_path=base_path,
                    list_index=i + 1,
                    indent=0,
                    test_suites=test_suites,
                ),
            )
        )
    if (
        "report_evaluation_scenario" in suite_def
        and suite_def.report_evaluation_scenario
    ):
        lines.extend(
            _render_scenario(
                suite_def.report_evaluation_scenario.scenario_type,
                TestSuiteRenderContext(
                    parent_yaml_file=suite_yaml_file,
                    parent_doc_file=suite_doc_file,
                    base_path=base_path,
                    list_index=i + 1,
                    indent=0,
                    test_suites=test_suites,
                ),
            )
        )
    lines.append("")

    lines.append(
        f"## [Checked requirements]({suites_readme_path}#checked-requirements)"
    )
    lines.append("")
    reqs = _collect_requirements_from_suite_def(suite_def)
    if not reqs:
        lines.append(
            "_This test suite documentation does not indicate that any requirements are checked._"
        )
    else:
        # Use an HTML table rather than Markdown table to enabled advanced features like spans
        lines.append("<table>")
        lines.append("  <tr>")
        lines.append(f'    <th><a href="{suites_readme_path}#package">Package</a></th>')
        lines.append(
            f'    <th><a href="{suites_readme_path}#requirement">Requirement</a></th>'
        )
        lines.append(f'    <th><a href="{suites_readme_path}#status">Status</a></th>')
        lines.append(
            f'    <th><a href="{suites_readme_path}#checked-in">Checked in</a></th>'
        )
        lines.append("  </tr>")

        req_ids_by_package: Dict[str, List[RequirementID]] = {}
        for req_id, req in reqs.items():
            package = req_ids_by_package.get(req_id.package(), [])
            if req_id not in package:
                package.append(req_id)
            req_ids_by_package[req_id.package()] = package

        for package in sorted(req_ids_by_package):
            req_md_path = os.path.relpath(
                req_ids_by_package[package][0].md_file_path(), start=base_path
            )
            package_caption = "<br>.".join(package.split("."))
            package_line = f'    <td rowspan="{len(req_ids_by_package[package])}" style="vertical-align:top;"><a href="{req_md_path}">{package_caption}</a></td>'
            for req_id in sorted(req_ids_by_package[package]):
                req_text = (
                    f'<a href="{req_md_path}">{req_id.short_requirement_name()}</a>'
                )

                has_todo = False
                has_complete = False
                scenarios = {}
                for checked_in in reqs[req_id].checked_in:
                    if checked_in.scenario.local_path not in scenarios:
                        scenarios[checked_in.scenario.name] = checked_in.scenario
                    if checked_in.check.has_todo:
                        has_todo = True
                    else:
                        has_complete = True
                if has_complete and not has_todo:
                    status_text = "Implemented"
                elif has_todo and not has_complete:
                    status_text = "TODO"
                elif has_todo and has_complete:
                    status_text = "Implemented + TODO"
                else:
                    status_text = "Not implemented"
                checked_in = list(
                    f'<a href="{os.path.relpath(scenarios[s].local_path, start=base_path)}">{scenarios[s].name}</a>'
                    for s in sorted(scenarios)
                )
                checked_in_text = f"{'<br>'.join(checked_in)}"

                lines.append("  <tr>")
                if package_line:
                    lines.append(package_line)
                    package_line = None
                lines.append(f"    <td>{req_text}</td>")
                lines.append(f"    <td>{status_text}</td>")
                lines.append(f"    <td>{checked_in_text}</td>")
                lines.append("  </tr>")
        lines.append("</table>")
    lines.append("")

    test_suites[suite_doc_file] = "\n".join(lines)
    return test_suites


def _render_scenario(
    scenario_type_name: TestScenarioTypeName, context: TestSuiteRenderContext
) -> List[str]:
    lines = []
    scenario_type = get_scenario_type_by_name(scenario_type_name)
    py_rel_path = os.path.relpath(inspect.getfile(scenario_type), context.base_path)
    scenario_doc = get_documentation(scenario_type)
    doc_rel_path = os.path.relpath(scenario_doc.local_path, start=context.base_path)
    lines.append(
        f"{' ' * context.indent}{context.list_index}. Scenario: [{scenario_doc.name}]({doc_rel_path}) ([`{scenario_type_name}`]({py_rel_path}))"
    )
    return lines


def _render_suite_by_type(
    suite_type: FileReference, context: TestSuiteRenderContext
) -> List[str]:
    lines = []
    suite_def = ImplicitDict.parse(
        load_dict_with_references(suite_type),
        TestSuiteDefinition,
    )
    suite_path = resolve_filename(suite_type)
    suite_rel_path = os.path.relpath(suite_path, start=context.base_path)
    doc_path = os.path.splitext(suite_path)[0] + ".md"
    doc_rel_path = os.path.relpath(doc_path, start=context.base_path)
    lines.append(
        f"{' ' * context.indent}{context.list_index}. Suite: [{suite_def.name}]({doc_rel_path}) ([`{suite_type}`]({suite_rel_path}))"
    )
    return lines


def _render_suite_by_definition(
    suite_def: TestSuiteDefinition, context: TestSuiteRenderContext
) -> List[str]:
    doc_path = (
        os.path.splitext(context.parent_doc_file)[0] + f"_suite{context.list_index}.md"
    )
    new_docs = make_test_suite_documentation(
        suite_def, context.parent_yaml_file, doc_path, context.parent_doc_file
    )

    for k, v in new_docs.items():
        context.test_suites[k] = v

    doc_rel_path = os.path.relpath(doc_path, context.base_path)
    parent_rel_path = os.path.relpath(context.parent_yaml_file, start=context.base_path)
    return [
        f"{' ' * context.indent}{context.list_index}. Suite: [{suite_def.name}]({doc_rel_path}) ([in-suite definition]({parent_rel_path}))"
    ]


def _render_action_generator(
    generator_def: Union[ActionGeneratorDefinition, PotentialActionGeneratorAction],
    context: TestSuiteRenderContext,
) -> List[str]:
    lines = []
    action_generator_type = action_generator_type_from_name(
        generator_def.generator_type
    )
    py_rel_path = os.path.relpath(
        inspect.getfile(action_generator_type), start=context.base_path
    )
    lines.append(
        f"{' ' * context.indent}{context.list_index}. Action generator: [`{generator_def.generator_type}`]({py_rel_path})"
    )
    potential_actions = list_potential_actions_for_action_generator_definition(
        generator_def
    )
    for j, potential_action in enumerate(potential_actions):
        lines.extend(
            _render_action(
                potential_action,
                TestSuiteRenderContext(
                    parent_yaml_file=context.parent_yaml_file,
                    parent_doc_file=context.parent_doc_file,
                    base_path=context.base_path,
                    list_index=j + 1,
                    indent=context.indent + 4,
                    test_suites=context.test_suites,
                ),
            )
        )
    return lines


def _render_action(
    action: Union[TestSuiteActionDeclaration, PotentialGeneratedAction],
    context: TestSuiteRenderContext,
) -> List[str]:
    action_type = action.get_action_type()
    if action_type == ActionType.TestScenario:
        return _render_scenario(action.test_scenario.scenario_type, context)
    elif action_type == ActionType.TestSuite:
        if "suite_type" in action.test_suite and action.test_suite.suite_type:
            return _render_suite_by_type(action.test_suite.suite_type, context)
        elif (
            "suite_definition" in action.test_suite
            and action.test_suite.suite_definition
        ):
            return _render_suite_by_definition(
                action.test_suite.suite_definition, context
            )
        else:
            raise ValueError(
                f"Test suite action {context.list_index} missing suite type or definition"
            )
    elif action_type == ActionType.ActionGenerator:
        return _render_action_generator(action.action_generator, context)
    else:
        raise NotImplementedError(f"Unsupported test suite action type: {action_type}")


@dataclass
class SuiteLocation(object):
    scenario: TestScenarioDocumentation
    check: TestCheckDocumentation


@dataclass
class RequirementInSuite(object):
    checked_in: List[SuiteLocation]

    def extend(self, other: RequirementInSuite):
        self.checked_in.extend(other.checked_in)


def _collect_requirements_from_suite_def(
    suite_def: TestSuiteDefinition,
) -> Dict[RequirementID, RequirementInSuite]:
    reqs: Dict[RequirementID, RequirementInSuite] = {}

    def combine(new_reqs: Dict[RequirementID, RequirementInSuite]) -> None:
        for req_id, req in new_reqs.items():
            if req_id not in reqs:
                reqs[req_id] = req
            else:
                reqs[req_id].extend(req)

    for action in suite_def.actions:
        combine(_collect_requirements_from_action(action))
    if (
        "report_evaluation_scenario" in suite_def
        and suite_def.report_evaluation_scenario
    ):
        combine(
            _collect_requirements_from_scenario(
                suite_def.report_evaluation_scenario.scenario_type
            )
        )
    return reqs


def _collect_requirements_from_action(
    action: Union[TestSuiteActionDeclaration, PotentialGeneratedAction]
) -> Dict[RequirementID, RequirementInSuite]:
    action_type = action.get_action_type()
    if action_type == ActionType.TestScenario:
        return _collect_requirements_from_scenario(action.test_scenario.scenario_type)
    elif action_type == ActionType.TestSuite:
        if "suite_type" in action.test_suite and action.test_suite.suite_type:
            suite_def = ImplicitDict.parse(
                load_dict_with_references(action.test_suite.suite_type),
                TestSuiteDefinition,
            )
            return _collect_requirements_from_suite_def(suite_def)
        elif (
            "suite_definition" in action.test_suite
            and action.test_suite.suite_definition
        ):
            return _collect_requirements_from_suite_def(
                action.test_suite.suite_definition
            )
        else:
            raise ValueError(
                "Neither suite_type nor suite_definition specified in test_suite action"
            )
    elif action_type == ActionType.ActionGenerator:
        return _collect_requirements_from_action_generator(action.action_generator)
    else:
        raise NotImplementedError(
            f"Test suite action type {action_type} not yet supported"
        )


def _collect_requirements_from_scenario(
    scenario_type: TestScenarioTypeName,
) -> Dict[RequirementID, RequirementInSuite]:
    docs = get_documentation_by_name(scenario_type)
    reqs: Dict[RequirementID, RequirementInSuite] = {}

    def add_req(req_id: RequirementID, check: TestCheckDocumentation) -> None:
        req = reqs.get(req_id, RequirementInSuite(checked_in=[]))
        req.checked_in.append(SuiteLocation(scenario=docs, check=check))
        reqs[req_id] = req

    for case in docs.cases:
        for step in case.steps:
            for check in step.checks:
                for req_id in check.applicable_requirements:
                    add_req(req_id, check)
    if "cleanup" in docs and docs.cleanup:
        for check in docs.cleanup.checks:
            for req_id in check.applicable_requirements:
                add_req(req_id, check)
    return reqs


def _collect_requirements_from_action_generator(
    generator_def: Union[ActionGeneratorDefinition, PotentialActionGeneratorAction]
) -> Dict[RequirementID, RequirementInSuite]:
    potential_actions = list_potential_actions_for_action_generator_definition(
        generator_def
    )

    reqs: Dict[RequirementID, RequirementInSuite] = {}
    for potential_action in potential_actions:
        new_reqs = _collect_requirements_from_action(potential_action)
        for req_id, req in new_reqs.items():
            if req_id not in reqs:
                reqs[req_id] = req
            else:
                reqs[req_id].extend(req)

    return reqs
