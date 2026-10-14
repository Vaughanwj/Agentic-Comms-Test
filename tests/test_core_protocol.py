from core.board import Outcome
from core.protocol import parse_message


def moves(text):
    parsed = parse_message(text)
    return [f.coord_text for f in parsed.fires], [(r.coord_text, r.outcome, r.ship_text) for r in parsed.results]


def test_standard_lines():
    assert moves("RESULT B7 HIT\nFIRE C4") == (["C4"], [("B7", Outcome.HIT, None)])
    assert moves("RESULT B7 MISS") == ([], [("B7", Outcome.MISS, None)])
    assert moves("RESULT B7 SUNK Destroyer") == ([], [("B7", Outcome.SUNK, "Destroyer")])
    assert moves("RESULT B7 SUNK") == ([], [("B7", Outcome.SUNK, None)])


def test_tolerates_light_formatting():
    assert moves("- **FIRE J10**") == (["J10"], [])
    assert moves("> RESULT: b7 - hit") == ([], [("B7", Outcome.HIT, None)])
    assert moves("fire b7") == (["B7"], [])  # lower case is fine at the start of a line
    assert moves("RESULT B7 HIT, FIRE C4") == (["C4"], [("B7", Outcome.HIT, None)])


def test_ignores_chat():
    assert moves("Ready to fire c4? Good luck!") == ([], [])
    assert moves("Hold your fire, the result was a hit.") == ([], [])
    assert moves("I'll FIRE when ready") == ([], [])
    assert moves("Ceasefire B7") == ([], [])


def test_sunk_ship_word_is_kept_raw():
    # The referee decides whether the word is a real ship name.
    assert moves("RESULT B7 SUNK FIRE C4")[1] == [("B7", Outcome.SUNK, "FIRE")]
