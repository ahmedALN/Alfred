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


# ------------------------------------------------ popups in the way


from src.windows.screens import dismiss_target, is_noise  # noqa: E402

UPDATE_POPUP = [
    _c("Text", "A new update is available!"),
    _c("Button", "Update now"),
    _c("Button", "Don't update yet"),
]

PROMO_POPUP = [
    _c("Text", "Special Offers"),
    _c("Button", "Weekend Deal. Jackbox Games"),
    _c("Button", "Close"),
]


def test_an_update_prompt_is_a_decision_not_an_obstacle():
    """MultiMC's update window swallows clicks meant for the instance
    list, which is why double-clicking an instance did nothing. Closing
    it quietly would hide a choice the user wants to make."""
    need = assess("MultiMC", UPDATE_POPUP)

    assert need.kind == "update"
    assert not is_noise("MultiMC", UPDATE_POPUP)
    assert "never" in need.as_dict()["instruction"].lower()


def test_a_promo_is_noise_and_goes_without_asking():
    assert is_noise("Special Offers", PROMO_POPUP)
    assert dismiss_target(PROMO_POPUP).name == "Close"


def test_a_sign_in_window_is_never_treated_as_noise():
    signin = [_c("Text", "Sign in to continue"), _c("Button", "Close")]

    assert not is_noise("Epic Games", signin)
    assert assess("Epic Games", signin).kind == "sign_in"


def test_dismissing_an_update_uses_the_decline_button():
    """If the user says "not now", that is the button - never 'Update
    now'."""
    assert dismiss_target(UPDATE_POPUP).name == "Don't update yet"


def test_a_window_with_no_way_out_offers_none():
    assert dismiss_target([_c("Text", "Loading...")]) is None


def test_an_ordinary_app_window_is_neither():
    normal = [_c("Button", "Store"), _c("ListItem", "Hades")]

    assert not is_noise("Steam", normal) and assess("Steam", normal) is None


def test_a_sign_in_button_on_a_busy_page_is_not_a_demand_to_sign_in():
    """Half the web has "Sign in" in its corner. Treating YouTube's as a
    prompt would stop Alfred on ordinary pages all day."""
    page = [_c("Button", "Sign in")] + [
        _c("Hyperlink", f"Some video number {i} 12 minutes") for i in range(40)
    ]

    assert assess("YouTube - Google Chrome", page) is None


def test_a_real_sign_in_screen_is_still_caught():
    """A dialog is small and says what it wants."""
    need = assess("Epic Games", [
        _c("Text", "Sign in to continue"), _c("Button", "Continue"),
        _c("Button", "Cancel"),
    ])
    assert need.kind == "sign_in"


def test_a_sign_in_titled_window_is_caught_however_big():
    need = assess("Sign in to Steam", [
        _c("Text", "Sign in")] + [_c("Button", f"thing {i}") for i in range(40)]
    )
    assert need.kind == "sign_in"


def test_terms_buried_in_a_footer_are_not_a_consent_prompt():
    page = [_c("Hyperlink", "Terms of Service")] + [
        _c("Hyperlink", f"Article number {i} about something") for i in range(40)
    ]
    assert assess("News - Google Chrome", page) is None
