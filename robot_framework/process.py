"""This module contains the main process of the robot."""

import os
from datetime import date

from OpenOrchestrator.orchestrator_connection.connection import OrchestratorConnection

from robot_framework import eflyt as framework
from itk_dev_shared_components import eflyt


def process(orchestrator_connection: OrchestratorConnection) -> None:
    """Do the primary process of the robot."""
    orchestrator_connection.log_trace("Running process.")

    orchestrator_connection.log_trace("Logging in to eflyt")
    browser = eflyt.login.login(orchestrator_connection)

    orchestrator_connection.log_trace("Searching cases")
    eflyt.search.search_cases(browser, case_state="I gang", case_status="Svarfrist overskredet", to_date=date.today().strftime("%d%m%Y"))

    cases = eflyt.search.extract_cases(browser)
    orchestrator_connection.log_info(f"Total cases found: {len(cases)}")
    cases = framework.filter_cases(cases)
    orchestrator_connection.log_info(f"Relevant cases found: {len(cases)}")

    for case in cases:
        framework.handle_case(browser, case, orchestrator_connection)
        framework.clear_downloads(orchestrator_connection)


if __name__ == '__main__':
    conn_string = os.getenv("OpenOrchestratorConnString")
    crypto_key = os.getenv("OpenOrchestratorKey")
    oc = OrchestratorConnection("Eflyt Test", conn_string, crypto_key, "")
    process(oc)
