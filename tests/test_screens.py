"""Recognising a screen that needs the user rather than the agent."""

from src.windows.screens import assess, draws_its_own_ui
from src.windows.uia import Control


def _c(ctype, name, password=False):
    return Control(0, ctype, name, "", (0, 0, 10, 10), True,
                   is_password=password)


# Exactly what Steam shows on a machine with two saved accounts.
STEAM_PICKER = [
    _c("Text", "Who's playing?"),
    _c("Hyperlink", "AhmedTWT"),
    _c("Text", "AhmedTWT"),
    _c("Text", "Account name: ahmedthefirstnoob"),
    _c("Hyperlink", "coty2001"),
    _c("Text", "coty2001"),
    _c("Hyperlink", "Add Account"),
]


def test_a_profile_picker_is_recognised_and_the_options_read_off():
    need = assess("Sign in to Steam", STEAM_PICKER)

    assert need.kind == "choose_profile"
    assert need.choices == ["AhmedTWT", "coty2001"]
    assert "which one" in need.as_dict()["instruction"]


def test_add_account_is_not_offered_as_a_profile():
    assert "Add Account" not in assess("Sign in to Steam", STEAM_PICKER).choices


def test_a_picker_beats_the_word_signin_in_the_title():
    """Steam's picker lives in a window titled "Sign in to Steam" - it
    is a choice, not a password prompt, and asking the user to sign in
    when their accounts are listed is the wrong question."""
    assert assess("Sign in to Steam", STEAM_PICKER).kind == "choose_profile"


def test_a_masked_field_is_a_sign_in_whatever_the_wording():
    need = assess("Who's playing?", [_c("Edit", "Password", password=True)])

    assert need.kind == "sign_in"
    assert "Do NOT type" in need.as_dict()["instruction"]


def test_a_sign_in_screen_is_recognised_by_its_words():
    assert assess("Epic Games", [_c("Text", "Sign in to continue")]).kind == (
        "sign_in")
    assert assess("Steam", [_c("Text", "Steam Guard code")]).kind == "sign_in"


def test_terms_are_never_accepted_on_the_users_behalf():
    need = assess("Setup", [_c("Text", "I agree to the licence agreement")])

    assert need.kind == "consent"
    assert "Never accept" in need.as_dict()["instruction"]


def test_an_ordinary_window_asks_nothing():
    assert assess("Steam", [
        _c("Button", "Store"), _c("Edit", "Search the store"),
        _c("ListItem", "Hades"),
    ]) is None


def test_a_window_with_only_its_own_frame_is_engine_drawn():
    """Roblox reports exactly this: no Play button exists to find."""
    assert draws_its_own_ui([
        _c("MenuItem", "System"), _c("Button", "Minimise"),
        _c("Button", "Restore"), _c("Button", "Close"),
    ])


def test_a_real_interface_is_not_mistaken_for_an_engine():
    assert not draws_its_own_ui([
        _c("MenuItem", "System"), _c("Button", "Close"),
        _c("Button", "Add Instance"), _c("ListItem", "1.21.11 Instance"),
    ])


def test_an_empty_tree_is_not_a_diagnosis():
    """Nothing at all means the window is not ready, which is a
    different problem with a different answer."""
    assert not draws_its_own_ui([])
