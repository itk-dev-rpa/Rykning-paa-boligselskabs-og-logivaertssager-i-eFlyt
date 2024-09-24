"""This module contains all logic related to the Eflyt system."""

from datetime import date, timedelta
import os
import time

import pypdf
from pypdf.errors import PyPdfError
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.select import Select
from OpenOrchestrator.orchestrator_connection.connection import OrchestratorConnection
from OpenOrchestrator.database.queues import QueueStatus
from itk_dev_shared_components.eflyt import eflyt_case, eflyt_search
from itk_dev_shared_components.eflyt.eflyt_case import Case
from itk_dev_shared_components.misc import file_util

from robot_framework import config, letters


def filter_cases(cases: list[Case]) -> list[Case]:
    """Filter cases from the case table.

    Args:
        cases: A list of cases to filter.

    Returns:
        A list of filtered case objects.
    """
    case_types_to_skip = (
        "Børneflytning 1",
        "Børneflytning 2",
        "Børneflytning 3",
        "Barn",
        "Mindreårig",
        "Sommerhus",
        "Nordisk land"
    )

    filtered_cases = [
        case for case in cases
        if case.deadline and case.deadline.date() < date.today()
        and ("Logivært" in case.case_types or "Boligselskab" in case.case_types)
        and not any(case_type in case_types_to_skip for case_type in case.case_types)
    ]

    return filtered_cases


def handle_case(browser: webdriver.Chrome, case: Case, orchestrator_connection: OrchestratorConnection) -> None:
    """Handle a single case with all steps included.

    Args:
        browser: The webdriver browser object.
        case: The case to handle.
        orchestrator_connection: The connection to Orchestrator.
    """
    if not check_queue(case, orchestrator_connection):
        return

    # Create a queue element to indicate the case is being handled
    queue_element = orchestrator_connection.create_queue_element(config.QUEUE_NAME, reference=case.case_number)
    orchestrator_connection.set_queue_element_status(queue_element.id, QueueStatus.IN_PROGRESS)

    orchestrator_connection.log_info(f"Beginning case: {case.case_number}")

    eflyt_search.open_case(browser, case.case_number)

    if not check_sagslog(browser):
        orchestrator_connection.set_queue_element_status(queue_element.id, QueueStatus.DONE, message="Sprunget over pga. sagslog.")
        orchestrator_connection.log_info("Skipping: Activity in sagslog.")
        return

    eflyt_case.change_tab(browser, tab_index=0)
    try:
        letter_title, logivaert_name = get_information_from_letter(browser)
    except PyPdfError:
        eflyt_case.add_note(browser, "Logiværtserklæringen kunne ikke læses.")
        orchestrator_connection.set_queue_element_status(queue_element.id, QueueStatus.DONE, message="Logiværtserklæringen kunne ikke læses.")
        return

    if "beboer" in letter_title:
        eflyt_case.change_tab(browser, tab_index=1)
        if not check_beboer(browser, logivaert_name):
            eflyt_case.add_note(browser, f"Logiværten, {logivaert_name}, bor ikke længere på adressen, så der er ikke afsendt en automatisk rykker.")
            orchestrator_connection.set_queue_element_status(queue_element.id, QueueStatus.DONE, message="Sprunget over da logivært ikke længere er beboer.")
            return

    if send_letter_to_logivaert(browser, letter_title, logivaert_name):
        eflyt_case.add_note(browser, f"Rykker sendt til logivært {logivaert_name}.")
    else:
        eflyt_case.add_note(browser, f"Brev kunne ikke sendes til logivært {logivaert_name}, da de ikke er tilmeldt digital post.")
        orchestrator_connection.set_queue_element_status(queue_element.id, QueueStatus.DONE, message="Logivært kan ikke modtage Digital Post.")
        return

    check_off_original_letter(browser)
    change_deadline(browser)
    eflyt_case.add_note(browser, "Deadline flyttet.")

    if send_letter_to_anmelder(browser, case, letter_title):
        eflyt_case.add_note(browser, "Brev sendt til anmelder.")
    else:
        eflyt_case.add_note(browser, "Brev kunne ikke sendes til anmelder, da de ikke er tilmeldt digital post.")
        orchestrator_connection.set_queue_element_status(queue_element.id, QueueStatus.DONE, message="Anmelder kan ikke modtage Digital Post.")
        return

    orchestrator_connection.set_queue_element_status(queue_element.id, QueueStatus.DONE, message="Sag færdigbehandlet.")


def check_queue(case: Case, orchestrator_connection: OrchestratorConnection) -> bool:
    """Check if a case has been handled before by checking the job queue i Orchestrator.

    Args:
        case: The case to check.
        orchestrator_connection: The connection to Orchestrator.

    Return:
        bool: True if the element should be handled, False if it should be skipped.
    """
    queue_elements = orchestrator_connection.get_queue_elements(queue_name=config.QUEUE_NAME, reference=case.case_number)

    if len(queue_elements) == 0:
        return True

    # If the case has been tried more than once before skip it
    if len(queue_elements) > 1:
        orchestrator_connection.log_info("Skipping: Case has failed in the past.")
        return False

    # If it has been marked as done, skip it
    if queue_elements[0].status == QueueStatus.DONE:
        orchestrator_connection.log_info("Skipping: Case already marked as done.")
        return False

    return True


def check_sagslog(browser: webdriver.Chrome) -> bool:
    """Check if a case should be handled based on the activities in the case log.

    Args:
        browser: The webdriver browser object.

    Returns:
        bool: True if the case should be handled, False if it should be skipped.
    """
    eflyt_case.change_tab(browser, tab_index=2)

    sagslog_table = browser.find_element(By.ID, "ctl00_ContentPlaceHolder2_ptFanePerson_sgcPersonTab_GridViewSagslog")
    rows = sagslog_table.find_elements(By.TAG_NAME, "tr")

    # Remove header row
    rows.pop(0)

    for row in rows:
        aktivitet = row.find_element(By.XPATH, "td[1]").text
        handling = row.find_element(By.XPATH, "td[4]/span[1]").text

        if aktivitet == "Online svar fra borger":
            return False

        if "Rykker - Logiværtserklæring" in handling:
            return False

    return True


def get_information_from_letter(browser: webdriver.Chrome) -> tuple[str]:
    """Find the latest letter sent. Extract the name of the letter and the receiver from the top of the letter.

    Args:
        browser: The webdriver browser object.

    Returns:
        (str, str): The title of the letter and the name of the receiver.

    Raises:
        PyPdfError: If the PDF file couldn't be read.
    """
    last_letter = browser.find_element(By.XPATH, '(//input[contains(@id, "_imbOpgave")])[last()]')
    last_letter.click()

    dir_path = os.path.join(os.path.expanduser("~"), "Downloads")
    file_path = file_util.wait_for_download(dir_path, file_name=None, file_extension=".pdf", timeout=20)

    try:
        reader = pypdf.PdfReader(file_path)
    except PyPdfError as e:
        os.remove(file_path)
        raise e

    text_parts = []

    # pylint: disable-next=unused-argument
    def visitor(text, cm, tm, fd, fs):
        """A visitor function that
        extracts all non whitespace text together with the y coordinate
        of the text.
        """
        y = tm[5]
        if text.strip():
            text = text.replace("\xa0", " ").strip()
            text_parts.append((y, text))

    reader.pages[0].extract_text(visitor_text=visitor)
    os.remove(file_path)

    # Get the top most text
    logivaert_name = sorted(text_parts)[0][1]
    logivaert_name = clean_name(logivaert_name)

    letter_title = last_letter.find_element(By.XPATH, "../..//span").text

    return (letter_title, logivaert_name)


def clean_name(name: str) -> str:
    """Clean up a name that might contain too many spaces.
    Reading from a pdf is sometimes messy.
    E.g. "Jo hn Do e" -> "John Doe".

    Args:
        name: The name to clean.

    Returns:
        The cleaned name.
    """
    # Remove all spaces from the input
    name = name.replace(" ", "")

    cleaned_name = ""

    # pylint: disable-next=consider-using-enumerate
    for i in range(len(name)):
        # Check if the character is uppercase, is not the first character,
        # and does not have a dash right before it
        if name[i].isupper() and i != 0 and name[i-1] != '-':
            cleaned_name += " "

        cleaned_name += name[i]

    return cleaned_name


def check_beboer(browser: webdriver.Chrome, beboer_name: str):
    """Check if the given person is on the list of beboere.
    The names are stripped of any whitespace to give a more precise result.

    Args:
        browser: The webdriver browser object.
        beboer_name: The name to find in the beboer list.

    Returns:
        True if the beboer_name is on the list.
    """
    beboer_table = browser.find_element(By.ID, "ctl00_ContentPlaceHolder2_ptFanePerson_becPersonTab_GridViewBeboere")
    rows = beboer_table.find_elements(By.TAG_NAME, "tr")
    rows.pop(0)

    for row in rows:
        name = row.find_element(By.XPATH, "td[3]").text
        if name.replace(" ", "") == beboer_name.replace(" ", ""):
            return True

    return False


def clear_downloads(orchestrator_connection: OrchestratorConnection):
    """Remove all pdf files in the downloads folder."""
    delete_count = 0
    error_count = 0

    dir_path = os.path.join(os.path.expanduser("~"), "Downloads")
    for file in os.listdir(dir_path):
        if file.endswith(".pdf"):
            file_path = os.path.join(dir_path, file)
            try:
                os.remove(file_path)
                delete_count += 1
            except PermissionError:
                error_count += 1

    orchestrator_connection.log_trace(f"{delete_count} files deleted from downloads folder. {error_count} files couldn't be deleted.")


def send_letter_to_anmelder(browser: webdriver.Chrome, case: Case, original_letter: str) -> bool:
    """Open the 'Breve' tab and send a letter to the anmelder.

    Args:
        browser: The webdriver browser object.
        case: The Case object.
        original_letter: The title of the original logiværtserklæring.

    Returns:
        bool: True if the letter was sent.
    """
    eflyt_case.change_tab(browser, tab_index=3)

    click_letter_template(browser, "- Logivært svarer ikke - brev til anmelder - partshø")

    time.sleep(0.2)  # In case the browser is a bit slow and won't read the dropdown properly
    # Select the anmelder as the receiver
    select_letter_receiver(browser, "(anmelder)")

    select_letter_language(browser, original_letter)

    # Click 'Send brev'
    browser.find_element(By.ID, "ctl00_ContentPlaceHolder2_ptFanePerson_bcPersonTab_btnSendBrev").click()

    # Insert the correct letter text
    text_area = browser.find_element(By.ID, "ctl00_ContentPlaceHolder2_ptFanePerson_bcPersonTab_txtStandardText")
    text_area.clear()
    text_area.send_keys(letters.LETTER_TO_ANMELDER)
    if "Boligselskab" in case.case_types:
        text_area.send_keys(letters.LETTER_TO_ANMELDER_BOLIGSELSKAB)
    else:
        text_area.send_keys(letters.LETTER_TO_ANMELDER_LOGIVAERT)

    # Click 'Ok'
    browser.find_element(By.ID, "ctl00_ContentPlaceHolder2_ptFanePerson_bcPersonTab_btnOK").click()

    # Check if a warning appears
    if check_digital_post_warning(browser):
        # Click 'Nej'
        browser.find_element(By.ID, "ctl00_ContentPlaceHolder2_ptFanePerson_bcPersonTab_btnDeleteLetter").click()
        return False

    # Click 'Ja'
    browser.find_element(By.ID, "ctl00_ContentPlaceHolder2_ptFanePerson_bcPersonTab_btnSaveLetter").click()
    return True


def send_letter_to_logivaert(browser: webdriver.Chrome, original_letter: str, logivaert_name: str) -> bool:
    """Open the 'Breve' tab and send a rykker to the logivært.

    Args:
        browser: The webdriver browser object.
        original_letter: The title of the original logiværtserklæring.
        logivaert_name: The name of the logivært.

    Returns:
        bool: True if the letter was sent.

    Raises:
        ValueError: If the correct letter template couldn't be found.
    """
    eflyt_case.change_tab(browser, tab_index=3)

    # Pick the correct letter template
    if "beboer" in original_letter and "manuel" in original_letter:
        template_name = "- Rykker - Logiværtserklæring beboer - Manuel"
    elif "beboer" in original_letter:
        template_name = "- Rykker - Logiværtserklæring beboer"
    elif "ejer" in original_letter and "manuel" in original_letter:
        template_name = "- Rykker - Logiværtserklæring ejer - Manuel"
    elif "ejer" in original_letter:
        template_name = "- Rykker - Logiværtserklæring ejer"
    else:
        raise ValueError("Unable to identify the correct letter template for rykker.")

    click_letter_template(browser, template_name)

    time.sleep(0.2)  # In case the browser is a bit slow and won't read the dropdown properly
    select_letter_receiver(browser, logivaert_name)
    select_letter_language(browser, original_letter)

    # Click 'Send brev'
    browser.find_element(By.ID, "ctl00_ContentPlaceHolder2_ptFanePerson_bcPersonTab_btnSendBrev").click()

    # Check if a warning appears
    if check_digital_post_warning(browser):
        # Click 'Nej'
        browser.find_element(By.ID, "ctl00_ContentPlaceHolder2_ptFanePerson_bcPersonTab_btnDeleteLetter").click()
        return False

    # Click 'Ja'
    browser.find_element(By.ID, "ctl00_ContentPlaceHolder2_ptFanePerson_bcPersonTab_btnSaveLetter").click()
    return True


def check_off_original_letter(browser: webdriver.Chrome) -> None:
    """Go to the 'Aktuel status' tab and check the checkbox next to the original logværtserklæring.

    Args:
        browser: The webdriver browser object.
    """
    eflyt_case.change_tab(browser, tab_index=0)
    opgave_table = browser.find_element(By.ID, "ctl00_ContentPlaceHolder2_ptFanePerson_moPersonTab_gvManuelOpfolgning")
    check_box = opgave_table.find_element(By.XPATH, "(//input[contains(@id, '_chkSvarmodtaget')])[last()]")
    check_box.click()


def change_deadline(browser: webdriver.Chrome) -> None:
    """Set the deadline to today's date plus 14 days.

    Args:
        browser: The webdriver browser object.
    """
    eflyt_case.change_tab(browser, tab_index=0)

    new_deadline = (date.today() + timedelta(days=14)).strftime("%d-%m-%Y")

    deadline_input = browser.find_element(By.ID, "ctl00_ContentPlaceHolder2_ptFanePerson_ncPersonTab_txtDeadline")
    deadline_input.clear()
    deadline_input.send_keys(new_deadline)

    browser.find_element(By.ID, "ctl00_ContentPlaceHolder2_ptFanePerson_ncPersonTab_btnDeadline").click()


def click_letter_template(browser: webdriver.Chrome, letter_name: str):
    """Click the letter template with the given name under the "Breve" tab.

    Args:
        browser: The webdriver browser object.
        letter_name: The literal name of the letter template to click.

    Raises:
        ValueError: If the letter wasn't found in the list.
    """
    letter_table = browser.find_element(By.ID, "ctl00_ContentPlaceHolder2_ptFanePerson_bcPersonTab_GridViewBreveNew")
    rows = letter_table.find_elements(By.TAG_NAME, "tr")

    for row in rows:
        text = row.find_element(By.XPATH, "td[2]").text
        if text == letter_name:
            row.find_element(By.XPATH, "td[1]/input").click()
            return

    raise ValueError(f"Template with the name '{letter_name}' was not found.")


def select_letter_language(browser: webdriver.Chrome, original_letter: str) -> None:
    """Select the letter language based on the language used in the original letter.

    Args:
        browser: The webdriver browser object.
        original_letter: The title of the original letter sent.
    """
    language_select = Select(browser.find_element(By.ID, "ctl00_ContentPlaceHolder2_ptFanePerson_bcPersonTab_ddlSprog"))
    if "(DA)" in original_letter:
        language_select.select_by_visible_text("Dansk")
    elif "(TY)" in original_letter:
        language_select.select_by_visible_text("Tysk")
    elif "(EN)" in original_letter:
        language_select.select_by_visible_text("Engelsk")


def select_letter_receiver(browser: webdriver.Chrome, receiver_name: str) -> None:
    """Select the receiver for the letter. The search is fuzzy so it's only checked
    if the options contains the receiver name.

    I some cases there's only one option for the receiver in which
    case there's a text label instead of a select element. In this
    case the predefined name is still checked against the desired receiver.

    Args:
        browser: The webdriver browser object.
        receiver_name: The name of the receiver to select.

    Raises:
        ValueError: If the given name isn't found in the select options.
        ValueError: If the given name doesn't match the static label.
    """
    # Check if there is a select for the receiver name
    name_select = browser.find_elements(By.ID, "ctl00_ContentPlaceHolder2_ptFanePerson_bcPersonTab_ddlModtager")
    if len(name_select) != 0:
        name_select = Select(name_select[0])
        for i, option in enumerate(name_select.options):
            if receiver_name in option.text:
                name_select.select_by_index(i)
                return

        raise ValueError(f"'{receiver_name}' wasn't found on the list of possible receivers.")

    # If there's simply a label for the receiver, check if the name matches
    name_label = browser.find_element(By.ID, "ctl00_ContentPlaceHolder2_ptFanePerson_bcPersonTab_lblModtagerName")
    if receiver_name not in name_label.text:
        raise ValueError(f"'{receiver_name}' didn't match the predefined receiver.")


def check_digital_post_warning(browser: webdriver.Chrome) -> bool:
    """Check if a red warning text has appeared warning that
    a letter must be sent manually.

    Args:
        browser: The webdriver browser object.

    Returns:
        bool: True if the warning has appeared.
    """
    warning_text = browser.find_elements(By.XPATH, "//font[@color='red']")
    return len(warning_text) != 0 and "Dokumentet skal sendes manuelt" in warning_text[0].text
