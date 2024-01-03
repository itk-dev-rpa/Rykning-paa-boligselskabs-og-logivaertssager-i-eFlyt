"""This module contains the main process of the robot."""

import os

from OpenOrchestrator.orchestrator_connection.connection import OrchestratorConnection

from robot_framework import eflyt


def process(orchestrator_connection: OrchestratorConnection) -> None:
    """Do the primary process of the robot."""
    orchestrator_connection.log_trace("Running process.")

    orchestrator_connection.log_trace("Logging in to eflyt")
    browser = eflyt.login(orchestrator_connection)

    orchestrator_connection.log_trace("Searching cases")
    eflyt.search_cases(browser)

    cases = eflyt.extract_cases(browser)
    orchestrator_connection.log_info(f"Relevant cases found: {len(cases)}")

    for case in cases:
        eflyt.handle_case(browser, case, orchestrator_connection)
        eflyt.clear_downloads(orchestrator_connection)


if __name__ == '__main__':
    conn_string = os.getenv("OpenOrchestratorConnString")
    crypto_key = os.getenv("OpenOrchestratorKey")
    oc = OrchestratorConnection("Eflyt Test", conn_string, crypto_key, "")
    process(oc)
