"""This module contains all logic related to the Eflyt system."""

from datetime import date, timedelta
from dataclasses import dataclass
import os
import time

import pypdf
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.select import Select
from OpenOrchestrator.orchestrator_connection.connection import OrchestratorConnection
from OpenOrchestrator.database.queues import QueueStatus

from robot_framework import config, letters


CASE_TYPES_TO_SKIP = (
    "Børneflytning 1",
    "Børneflytning 2",
    "Børneflytning 3",
    "Barn",
    "Mindreårig",
    "Sommerhus",
    "Nordisk land"
)


@dataclass
class Case:
    """A dataclass representing an Eflyt case."""
    case_number: str
    deadline: str
    case_types: list[str]


def login(orchestrator_connection: OrchestratorConnection) -> webdriver.Chrome:
    """Opens a browser and logs in to Eflyt.

    Args:
        orchestrator_connection: The connection to Orchestrator.

    Returns:
        A selenium browser object.
    """
    eflyt_creds = orchestrator_connection.get_credential(config.EFLYT_CREDS)

    browser = webdriver.Chrome()
    browser.maximize_window()
    browser.get("https://notuskommunal.scandihealth.net/")

    user_field = browser.find_element(By.ID, "Login1_UserName")
    user_field.send_keys(eflyt_creds.username)

    pass_field = browser.find_element(By.ID, "Login1_Password")
    pass_field.send_keys(eflyt_creds.password)

    browser.find_element(By.ID, "Login1_LoginImageButton").click()

    browser.get("https://notuskommunal.scandihealth.net/web/SearchResulteFlyt.aspx")

    return browser


def search_cases(browser: webdriver.Chrome) -> None:
    """Apply the correct filters in Eflyt and search the case list.

    Args:
        browser: The webdriver browser object.
    """
    sagstilstand_select = Select(browser.find_element(By.ID, "ctl00_ContentPlaceHolder1_SearchControl_ddlTilstand"))
    sagstilstand_select.select_by_visible_text("I gang")

    status_select = Select(browser.find_element(By.ID, "ctl00_ContentPlaceHolder1_SearchControl_ddlStatus"))
    status_select.select_by_visible_text("Svarfrist overskredet")

    search_date = date.today().strftime("%d%m%Y")
    date_input = browser.find_element(By.ID, "ctl00_ContentPlaceHolder1_SearchControl_txtFlytteEndDato")
    date_input.send_keys(search_date)

    search_button = browser.find_element(By.ID, "ctl00_ContentPlaceHolder1_SearchControl_btnSearch")
    search_button.click()


def extract_cases(browser: webdriver.Chrome) -> list[Case]:
    """Extract and filter cases from the case table.

    Args:
        browser: The webdriver browser object.

    Returns:
        A list of filtered case objects.
    """
    table = browser.find_element(By.ID, "ctl00_ContentPlaceHolder2_GridViewSearchResult")
    rows = table.find_elements(By.TAG_NAME, "tr")

    # remove header row
    rows.pop(0)

    cases = []
    for row in rows:
        deadline = row.find_element(By.XPATH, "td[3]/a").text

        if not deadline:
            continue

        # Convert deadline to date object
        day, month, year = deadline.split("-")
        deadline = date(int(year), int(month), int(day))

        # Check if deadline is passed
        if deadline >= date.today():
            continue

        case_number = row.find_element(By.XPATH, "td[4]").text
        case_types_text = row.find_element(By.XPATH, "td[5]").text

        # If the case types ends with '...' we need to get the title instead
        if case_types_text.endswith("..."):
            case_types_text = row.find_element(By.XPATH, "td[5]").get_attribute("Title")

        case_types = case_types_text.split(", ")

        # Check if the case has a case type to skip
        if any(case_type in CASE_TYPES_TO_SKIP for case_type in case_types):
            continue

        # Check if the case has either Logivært or Boligselskab as case type
        if not ("Logivært" in case_types or "Boligselskab" in case_types):
            continue

        cases.append(Case(case_number, deadline, case_types))

    return cases


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

    open_case(browser, case)

    change_tab(browser, tab_index=2)
    if not check_sagslog(browser):
        orchestrator_connection.set_queue_element_status(queue_element.id, QueueStatus.DONE, message="Skipped due to activity in sagslog.")
        return

    change_tab(browser, tab_index=0)
    letter_title, logivaert_name = get_information_from_letter(browser)

    today = date.today().strftime("%d-%m-%Y")

    if "beboer" in letter_title:
        change_tab(browser, tab_index=1)
        if not check_beboer(browser, logivaert_name):
            change_tab(browser, tab_index=0)
            create_note(browser, f"{today} Besked fra robot: Logiværten bor ikke længere på adressen, så der er ikke afsendt en automatisk rykker.")
            orchestrator_connection.set_queue_element_status(queue_element.id, QueueStatus.DONE, message="Skipped due to logivært no longer living on address.")
            return

    change_tab(browser, tab_index=3)
    send_letter_to_anmelder(browser, case, letter_title)
    send_letter_to_logivaert(browser, letter_title, logivaert_name)

    change_tab(browser, tab_index=0)
    check_off_original_letter(browser)
    change_deadline(browser)
    create_note(browser, f"{today} Besked fra robot: Automatisk rykker sendt til logivært {logivaert_name}, brev sendt til anmelder og deadline flyttet.")

    orchestrator_connection.set_queue_element_status(queue_element.id, QueueStatus.DONE, message="Case handled successfully.")


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
        return False

    # If it has been marked as done, skip it
    if queue_elements[0].status == QueueStatus.DONE:
        return False

    return True


def open_case(browser: webdriver.Chrome, case: Case):
    """Open a case by searching for it's case number.

    Args:
        browser: The webdriver browser object.
        case: The case to open.
    """
    # The id for both the search field and search button changes based on the current view hence the weird selectors.
    case_input = browser.find_element(By.XPATH, '//input[contains(@id, "earchControl_txtSagNr")]')
    case_input.clear()
    case_input.send_keys(case.case_number)

    browser.find_element(By.XPATH, '//input[contains(@id, "earchControl_btnSearch")]').click()


def check_sagslog(browser: webdriver.Chrome) -> bool:
    """Check if a case should be handled based on the activities in the case log.

    Args:
        browser: The webdriver browser object.

    Returns:
        bool: True if the case should be handled, False if it should be skipped.
    """
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
    """
    last_letter = browser.find_element(By.XPATH, '(//input[contains(@id, "_imbOpgave")])[last()]')
    click_time = time.time()
    last_letter.click()

    file_path = wait_for_download(click_time)
    reader = pypdf.PdfReader(file_path)

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

    letter_title = last_letter.find_element(By.XPATH, "../..//span").text

    return (letter_title, logivaert_name)


def wait_for_download(start_time: float):
    """Check the downloads folder every second for a file newer than start_time.

    Args:
        start_time: The time after which the file should appear.

    Raises:
        TimeoutError: If the file didn't appear within 20 seconds.

    Returns:
        The path of the file.
    """
    dir_path = os.path.join(os.path.expanduser("~"), "Downloads")

    # Wait for up to 20 seconds for a new file to appear
    for _ in range(20):
        time.sleep(1)
        for file in os.listdir(dir_path):
            file_path = os.path.join(dir_path, file)
            if file.endswith(".pdf") and os.path.getmtime(file_path) > start_time:
                return file_path

    raise TimeoutError(f"No file was detected in {dir_path} after 20 seconds.")


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


def check_beboer(browser: webdriver.Chrome, beboer_name: str):
    """Check if the given person is on the list of beboere.

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
        if name == beboer_name:
            return True

    return False


def send_letter_to_anmelder(browser: webdriver.Chrome, case: Case, original_letter: str):
    """Open the 'Breve' tab and send a letter to the anmelder.

    Args:
        browser: The webdriver browser object.
        case: The Case object.
        original_letter: The title of the original logiværtserklæring.
    """
    click_letter_template(browser, "- Logivært svarer ikke - brev til anmelder - partshø")

    # Select the anmelder as the receiver
    select_letter_receiver(browser, "(anmelder)")

    select_letter_language(browser, original_letter)

    browser.find_element(By.ID, "ctl00_ContentPlaceHolder2_ptFanePerson_bcPersonTab_btnSendBrev").click()

    # Insert the correct letter text
    text_area = browser.find_element(By.ID, "ctl00_ContentPlaceHolder2_ptFanePerson_bcPersonTab_txtStandardText")
    text_area.clear()
    text_area.send_keys(letters.LETTER_TO_ANMELDER)
    if "Boligselskab" in case.case_types:
        text_area.send_keys(letters.LETTER_TO_ANMELDER_BOLIGSELSKAB)
    else:
        text_area.send_keys(letters.LETTER_TO_ANMELDER_LOGIVAERT)

    browser.find_element(By.ID, "ctl00_ContentPlaceHolder2_ptFanePerson_bcPersonTab_btnOK").click()
    browser.find_element(By.ID, "ctl00_ContentPlaceHolder2_ptFanePerson_bcPersonTab_btnSaveLetter").click()


def send_letter_to_logivaert(browser: webdriver.Chrome, original_letter: str, logivaert_name: str) -> None:
    """Open the 'Breve' tab and send a rykker to the logivært.

    Args:
        browser: The webdriver browser object.
        original_letter: The title of the original logiværtserklæring.
        logivaert_name: The name of the logivært.

    Raises:
        ValueError: If the correct letter template couldn't be found.
    """
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

    select_letter_receiver(browser, logivaert_name)
    select_letter_language(browser, original_letter)

    browser.find_element(By.ID, "ctl00_ContentPlaceHolder2_ptFanePerson_bcPersonTab_btnSendBrev").click()
    browser.find_element(By.ID, "ctl00_ContentPlaceHolder2_ptFanePerson_bcPersonTab_btnSaveLetter").click()


def check_off_original_letter(browser: webdriver.Chrome) -> None:
    """Go to the 'Aktuel status' tab and check the checkbox next to the original logværtserklæring.

    Args:
        browser: The webdriver browser object.
    """
    opgave_table = browser.find_element(By.ID, "ctl00_ContentPlaceHolder2_ptFanePerson_moPersonTab_gvManuelOpfolgning")
    check_box = opgave_table.find_element(By.XPATH, "(//input[contains(@id, '_chkSvarmodtaget')])[last()]")
    check_box.click()


def change_deadline(browser: webdriver.Chrome) -> None:
    """Set the deadline to today's date plus 14 days.

    Args:
        browser: The webdriver browser object.
    """
    new_deadline = (date.today() + timedelta(days=14)).strftime("%d-%m-%Y")

    deadline_input = browser.find_element(By.ID, "ctl00_ContentPlaceHolder2_ptFanePerson_ncPersonTab_txtDeadline")
    deadline_input.clear()
    deadline_input.send_keys(new_deadline)

    browser.find_element(By.ID, "ctl00_ContentPlaceHolder2_ptFanePerson_ncPersonTab_btnDeadline").click()


def change_tab(browser: webdriver.Chrome, tab_index: int):
    """Change the tab in the case view e.g. 'Sagslog', 'Breve'.

    Args:
        browser: The webdriver browser object.
        tab_index: The zero-based index of the tab to select.
    """
    browser.execute_script(f"__doPostBack('ctl00$ContentPlaceHolder2$ptFanePerson$ImgJournalMap','{tab_index}')")


def create_note(browser: webdriver.Chrome, note_text: str):
    """Create a note on the case."""
    browser.find_element(By.ID, "ctl00_ContentPlaceHolder2_ptFanePerson_ncPersonTab_ButtonVisOpdater").click()

    text_area = browser.find_element(By.ID, "ctl00_ContentPlaceHolder2_ptFanePerson_ncPersonTab_txtVisOpdaterNote")

    text_area.send_keys(note_text)
    text_area.send_keys("\n\n")

    browser.find_element(By.ID, "ctl00_ContentPlaceHolder2_ptFanePerson_ncPersonTab_btnLongNoteUpdater").click()


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
