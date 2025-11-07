"""This module contains the main process of the robot."""

import os
from datetime import date

from OpenOrchestrator.orchestrator_connection.connection import OrchestratorConnection
from itk_dev_shared_components.eflyt import eflyt_login, eflyt_search
import itk_dev_event_log

from robot_framework import eflyt


def process(orchestrator_connection: OrchestratorConnection) -> None:
    """Do the primary process of the robot."""
    orchestrator_connection.log_trace("Running process.")

    event_log = orchestrator_connection.get_constant("Event Log")
    itk_dev_event_log.setup_logging(event_log.value)

    orchestrator_connection.log_trace("Logging in to eflyt")
    credentials = orchestrator_connection.get_credential("Eflyt")
    browser = eflyt_login.login(credentials.username, credentials.password)

    orchestrator_connection.log_trace("Searching cases")
    eflyt_search.search(browser, case_state="I gang", case_status="Svarfrist overskredet", to_date=date.today())

    cases = eflyt_search.extract_cases(browser)
    orchestrator_connection.log_info(f"Total cases found: {len(cases)}")
    cases = eflyt.filter_cases(cases)
    orchestrator_connection.log_info(f"Relevant cases found: {len(cases)}")

    for case in cases:
        eflyt.handle_case(browser, case, orchestrator_connection)
        eflyt.clear_downloads(orchestrator_connection)


if __name__ == '__main__':
    conn_string = os.getenv("OpenOrchestratorConnString")
    crypto_key = os.getenv("OpenOrchestratorKey")
    oc = OrchestratorConnection("Eflyt Test", conn_string, crypto_key, "", "")
    process(oc)
