from datetime import datetime, timedelta
from typing import Optional

from uas_standards.astm.f3548.v21.api import (
    EntityID,
    OperationalIntentReference,
    OperationalIntentState,
    PutOperationalIntentReferenceParameters,
    Subscription,
    SubscriptionID,
)
from uas_standards.astm.f3548.v21.constants import Scope

from monitoring.monitorlib.fetch import QueryError
from monitoring.monitorlib.geotemporal import Volume4D
from monitoring.monitorlib.subscription_params import SubscriptionParams
from monitoring.prober.infrastructure import register_resource_type
from monitoring.uss_qualifier.resources import PlanningAreaResource
from monitoring.uss_qualifier.resources.astm.f3548.v21.dss import (
    DSSInstance,
    DSSInstanceResource,
)
from monitoring.uss_qualifier.resources.communications import ClientIdentityResource
from monitoring.uss_qualifier.resources.interuss.id_generator import IDGeneratorResource
from monitoring.uss_qualifier.resources.planning_area import PlanningAreaSpecification
from monitoring.uss_qualifier.scenarios.astm.utm.dss import test_step_fragments
from monitoring.uss_qualifier.scenarios.astm.utm.dss.fragments.oir import (
    crud as oir_fragments,
)
from monitoring.uss_qualifier.scenarios.astm.utm.dss.fragments.oir import (
    step_oir_has_correct_subscription,
)
from monitoring.uss_qualifier.scenarios.astm.utm.dss.fragments.sub import (
    crud as sub_fragments,
)
from monitoring.uss_qualifier.scenarios.scenario import TestScenario
from monitoring.uss_qualifier.suites.suite import ExecutionContext


class OIRExplicitSubHandling(TestScenario):
    OIR_TYPE = register_resource_type(401, "Operational Intent Reference")
    SUB_TYPE = register_resource_type(402, "Subscription")
    EXTRA_SUB_TYPE = register_resource_type(403, "Subscription")
    _dss: DSSInstance

    _oir_id: EntityID
    _sub_id: SubscriptionID
    _extra_sub_id: SubscriptionID

    # Keep track of the current OIR state
    _current_oir: Optional[OperationalIntentReference]
    _expected_manager: str
    _planning_area: PlanningAreaSpecification
    _planning_area_volume4d: Volume4D

    # Keep track of the current subscription
    _sub_params: Optional[SubscriptionParams]
    _current_sub: Optional[Subscription]

    _current_extra_sub: Optional[Subscription]

    def __init__(
        self,
        dss: DSSInstanceResource,
        id_generator: IDGeneratorResource,
        client_identity: ClientIdentityResource,
        planning_area: PlanningAreaResource,
    ):
        """
        Args:
            dss: dss to test
            id_generator: will let us generate specific identifiers
            client_identity: Provides the identity of the client that will be used to create the OIRs
            planning_area: An Area to use for the tests. It should be an area for which the DSS is responsible,
                 but has no other requirements.
        """
        super().__init__()
        scopes = {
            Scope.StrategicCoordination: "create and delete operational intent references"
        }
        # This is an UTMClientSession
        self._dss = dss.get_instance(scopes)
        self._pid = [self._dss.participant_id]

        self._oir_id = id_generator.id_factory.make_id(self.OIR_TYPE)
        self._sub_id = id_generator.id_factory.make_id(self.SUB_TYPE)
        self._extra_sub_id = id_generator.id_factory.make_id(self.EXTRA_SUB_TYPE)

        self._expected_manager = client_identity.subject()

        self._planning_area = planning_area.specification

        self._planning_area_volume4d = Volume4D(
            volume=self._planning_area.volume,
        )

    def run(self, context: ExecutionContext):
        self.begin_test_scenario(context)
        self.begin_test_case("Setup")
        self._ensure_clean_workspace()
        self.end_test_case()

        self.begin_test_case("Validate explicit subscription on OIR creation")
        self._step_create_explicit_sub()
        self._step_create_oir_insufficient_subscription()
        self._step_create_oir_sufficient_subscription()
        self.end_test_case()

        self.begin_test_case(
            "Validate explicit subscription upon subscription replacement"
        )
        self._steps_update_oir_with_insufficient_explicit_sub(is_replacement=True)
        self._step_update_oir_with_sufficient_explicit_sub(is_replacement=True)
        self._clean_test_case()
        self.end_test_case()

        self.begin_test_case(
            "OIR in ACCEPTED state can be created without subscription"
        )
        self.begin_test_step("Create an operational intent reference")
        self._current_oir, _, _ = oir_fragments.create_oir_query(
            scenario=self,
            dss=self._dss,
            oir_id=self._oir_id,
            oir_params=self._planning_area.get_new_operational_intent_ref_params(
                key=[],
                state=OperationalIntentState.Accepted,
                uss_base_url=self._planning_area.get_base_url(),
                time_start=datetime.now() - timedelta(seconds=10),
                time_end=datetime.now() + timedelta(minutes=20),
                subscription_id=None,
                implicit_sub_base_url=None,
            ),
        )
        self.end_test_step()
        self._step_oir_has_correct_subscription(expected_sub_id=None)
        self.end_test_case()

        self.begin_test_case(
            "Validate explicit subscription being attached to OIR without subscription"
        )
        self._steps_update_oir_with_insufficient_explicit_sub(is_replacement=False)
        self._step_oir_has_correct_subscription(expected_sub_id=None)
        self._step_update_oir_with_sufficient_explicit_sub(is_replacement=False)
        self._step_oir_has_correct_subscription(expected_sub_id=self._extra_sub_id)
        self.end_test_case()

        self.begin_test_case("Remove explicit subscription from OIR")
        self._step_remove_subscription_from_oir()
        self._step_oir_has_correct_subscription(expected_sub_id=None)
        self.end_test_case()

        self.end_test_scenario()

    def _step_remove_subscription_from_oir(self):
        self.begin_test_step("Remove explicit subscription from OIR")
        oir_update_params = self._planning_area.get_new_operational_intent_ref_params(
            key=[],
            state=OperationalIntentState.Accepted,
            uss_base_url=self._planning_area.get_base_url(),
            time_start=self._current_oir.time_start.value.datetime,
            time_end=self._current_oir.time_end.value.datetime,
            subscription_id=None,
        )
        with self.check(
            "Mutate operational intent reference query succeeds",
            self._pid,
        ) as check:
            try:
                mutated_oir, _, q = self._dss.put_op_intent(
                    extents=oir_update_params.extents,
                    key=oir_update_params.key,
                    state=oir_update_params.state,
                    base_url=oir_update_params.uss_base_url,
                    oi_id=self._oir_id,
                    subscription_id=None,
                    ovn=self._current_oir.ovn,
                    force_no_implicit_subscription=True,
                )
                self.record_query(q)
                self._current_oir = mutated_oir
            except QueryError as qe:
                self.record_queries(qe.queries)
                check.record_failed(
                    summary="Removal of explicit subscription from OIR failed",
                    details=f"Was expecting an HTTP 200 response for a mutation with valid parameters, but got {qe.cause_status_code} instead. {qe.msg}",
                    query_timestamps=qe.query_timestamps,
                )
        self.end_test_step()

    def _step_create_explicit_sub(self):
        self._sub_params = self._planning_area.get_new_subscription_params(
            subscription_id=self._sub_id,
            start_time=datetime.now() - timedelta(seconds=10),
            duration=timedelta(minutes=20),
            notify_for_op_intents=True,
            notify_for_constraints=False,
        )
        self.begin_test_step("Create independent subscription")
        self._current_sub, _, _ = sub_fragments.sub_create_query(
            scenario=self, dss=self._dss, sub_params=self._sub_params
        )
        self.end_test_step()

    def _step_create_oir_insufficient_subscription(self):
        self.begin_test_step(
            "Provide subscription not covering extent of OIR being created"
        )

        oir_params = self._planning_area.get_new_operational_intent_ref_params(
            key=[],
            state=OperationalIntentState.Accepted,
            uss_base_url=self._planning_area.get_base_url(),
            time_start=datetime.now() - timedelta(seconds=10),
            time_end=self._sub_params.end_time
            + timedelta(seconds=1),  # OIR ends 1 sec after subscription
            subscription_id=self._sub_id,
        )

        with self.check(
            "Request to create OIR with too short subscription fails", self._pid
        ) as check:
            try:
                _, _, q = self._dss.put_op_intent(
                    extents=oir_params.extents,
                    key=oir_params.key,
                    state=oir_params.state,
                    base_url=oir_params.uss_base_url,
                    oi_id=self._oir_id,
                    subscription_id=oir_params.subscription_id,
                )
                self.record_query(q)
                # We don't expect to reach this point:
                check.record_failed(
                    summary="OIR creation with too short subscription was not expected to succeed",
                    details=f"Was expecting an HTTP 400 response because of an insufficient subscription, but got {q.status_code} instead",
                    query_timestamps=[q.request.timestamp],
                )
            except QueryError as qe:
                self.record_queries(qe.queries)
                if qe.cause_status_code == 400:
                    pass
                else:
                    check.record_failed(
                        summary="OIR creation with too short subscription failed for unexpected reason",
                        details=f"Was expecting an HTTP 400 response because of an insufficient subscription, but got {qe.cause_status_code} instead",
                        query_timestamps=qe.query_timestamps,
                    )

        self.end_test_step()

    def _step_create_oir_sufficient_subscription(self):
        self.begin_test_step("Create an OIR with correct explicit subscription")
        self._current_oir, _, _ = oir_fragments.create_oir_query(
            scenario=self,
            dss=self._dss,
            oir_id=self._oir_id,
            oir_params=self._planning_area.get_new_operational_intent_ref_params(
                key=[],
                state=OperationalIntentState.Accepted,
                uss_base_url=self._planning_area.get_base_url(),
                time_start=datetime.now() - timedelta(seconds=10),
                time_end=self._sub_params.end_time
                - timedelta(seconds=60),  # OIR ends at the same time as subscription
                subscription_id=self._sub_id,
            ),
        )
        self.end_test_step()

        self._step_oir_has_correct_subscription(expected_sub_id=self._sub_id)

    def _steps_update_oir_with_insufficient_explicit_sub(self, is_replacement: bool):
        # Create another subscription that is a few seconds short of covering the OIR:
        oir_duration = (
            self._current_oir.time_end.value.datetime
            - self._current_oir.time_start.value.datetime
        )
        new_sub_params = self._planning_area.get_new_subscription_params(
            subscription_id=self._extra_sub_id,
            start_time=datetime.now() - timedelta(seconds=10),
            duration=oir_duration - timedelta(seconds=2),
            notify_for_op_intents=True,
            notify_for_constraints=False,
        )

        self.begin_test_step("Create a subscription")
        self._current_extra_sub, _, _ = sub_fragments.sub_create_query(
            scenario=self, dss=self._dss, sub_params=new_sub_params
        )
        self.end_test_step()

        # Now attempt to mutate the OIR for it to use the invalid subscription:
        oir_update_params = self._planning_area.get_new_operational_intent_ref_params(
            key=[],
            state=OperationalIntentState.Accepted,
            uss_base_url=self._planning_area.get_base_url(),
            time_start=self._current_oir.time_start.value.datetime,
            time_end=self._current_oir.time_end.value.datetime,
            subscription_id=self._extra_sub_id,
        )
        step_name = (
            "Attempt to replace OIR's existing explicit subscription with an insufficient one"
            if is_replacement
            else "Attempt to attach insufficient subscription to OIR"
        )
        self.begin_test_step(step_name)
        check_name = (
            "Request to mutate OIR while providing a too short subscription fails"
            if is_replacement
            else "Request to attach insufficient subscription to OIR fails"
        )
        with self.check(
            check_name,
            self._pid,
        ) as check:
            try:
                _, _, q = self._dss.put_op_intent(
                    extents=oir_update_params.extents,
                    key=oir_update_params.key,
                    state=oir_update_params.state,
                    base_url=oir_update_params.uss_base_url,
                    oi_id=self._oir_id,
                    subscription_id=oir_update_params.subscription_id,
                    ovn=self._current_oir.ovn,
                )
                self.record_query(q)
                # We don't expect to reach this point:
                check.record_failed(
                    summary="Request for OIR with too short subscription was not expected to succeed",
                    details=f"Was expecting an HTTP 400 response because of an insufficient subscription, but got {q.status_code} instead",
                    query_timestamps=[q.request.timestamp],
                )
            except QueryError as qe:
                self.record_queries(qe.queries)
                if qe.cause_status_code == 400:
                    pass
                else:
                    check.record_failed(
                        summary="Request for OIR with too short subscription failed for unexpected reason",
                        details=f"Was expecting an HTTP 400 response because of an insufficient subscription, but got {qe.cause_status_code} instead. {qe.msg}",
                        query_timestamps=qe.query_timestamps,
                    )
        self.end_test_step()

    def _step_update_oir_with_sufficient_explicit_sub(self, is_replacement: bool):
        step_name = (
            "Replace the OIR's explicit subscription"
            if is_replacement
            else "Attach explicit subscription to OIR"
        )
        self.begin_test_step(step_name)
        self._current_oir, _, _ = oir_fragments.update_oir_query(
            scenario=self,
            dss=self._dss,
            oir_id=self._oir_id,
            ovn=self._current_oir.ovn,
            oir_params=self._planning_area.get_new_operational_intent_ref_params(
                key=[],
                state=OperationalIntentState.Accepted,
                uss_base_url=self._planning_area.get_base_url(),
                time_start=self._current_extra_sub.time_start.value.datetime,
                time_end=self._current_extra_sub.time_end.value.datetime,
                subscription_id=self._extra_sub_id,
            ),
        )
        self.end_test_step()

    def _step_oir_has_correct_subscription(
        self, expected_sub_id: Optional[SubscriptionID]
    ):
        step_oir_has_correct_subscription(
            self,
            self._dss,
            self._oir_id,
            expected_sub_id,
        )

    def _delete_subscription(self, sub_id: EntityID, sub_version: str):
        with self.check("Subscription can be deleted", self._pid) as check:
            sub = self._dss.delete_subscription(sub_id, sub_version)
            self.record_query(sub)
            if sub.status_code != 200:
                check.record_failed(
                    summary="Could not delete subscription",
                    details=f"Failed to delete subscription with error code {sub.status_code}: {sub.error_message}",
                    query_timestamps=[sub.request.timestamp],
                )

    def _ensure_clean_workspace(self):
        self.begin_test_step("Cleanup OIRs")
        self._clean_all_oirs()
        self.end_test_step()
        self.begin_test_step("Cleanup Subscriptions")
        self._clean_all_subs()
        self.end_test_step()

    def _clean_all_oirs(self):
        # Delete any active OIR we might own
        test_step_fragments.cleanup_active_oirs(
            self,
            self._dss,
            self._planning_area_volume4d.to_f3548v21(),
            self._expected_manager,
        )

        # Make sure the OIR IDs we are going to use are available
        test_step_fragments.cleanup_op_intent(self, self._dss, self._oir_id)

    def _clean_all_subs(self):
        # Delete any active subscription we might own
        test_step_fragments.cleanup_active_subs(
            self,
            self._dss,
            self._planning_area_volume4d.to_f3548v21(),
        )

        # Make sure the subscription IDs we are going to use are available
        test_step_fragments.cleanup_sub(self, self._dss, self._sub_id)
        test_step_fragments.cleanup_sub(self, self._dss, self._extra_sub_id)

    def _clean_test_case(self):
        self.begin_test_step("Cleanup After Test Case")
        oir_fragments.delete_oir_query(
            scenario=self, dss=self._dss, oir_id=self._oir_id, ovn=self._current_oir.ovn
        )
        self._current_oir = None
        self._delete_subscription(self._extra_sub_id, self._current_extra_sub.version)
        self._current_sub = None
        self.end_test_step()

    def cleanup(self):
        self.begin_cleanup()
        self._clean_all_oirs()
        self._clean_all_subs()
        self.end_cleanup()

    def _default_oir_params(
        self, subscription_id: SubscriptionID
    ) -> PutOperationalIntentReferenceParameters:
        return self._planning_area.get_new_operational_intent_ref_params(
            key=[],
            state=OperationalIntentState.Accepted,
            uss_base_url=self._planning_area.get_base_url(),
            time_start=datetime.now() - timedelta(seconds=10),
            time_end=datetime.now() + timedelta(minutes=20),
            subscription_id=subscription_id,
        )
