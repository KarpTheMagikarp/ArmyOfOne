"""
The Mods drawer, which is where somebody decides what to install.

Everything here came from watching a real person use it. The row that opens
did not look like it opened, and seven labels said nothing about Enemies being
six times the size of Weapons, which is the one thing worth knowing before
ticking anything.
"""

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def ui():
    return (REPO / "ui" / "index.html").read_text(encoding="utf-8")


def test_the_row_that_opens_looks_like_it_opens():
    """
    a row of text and "6/7" on the right. Nothing about that says there is
    anything behind it, and people were not finding the toggles.
    """
    text = ui()
    assert "#mdExtras .mod.has-opts .chev{" in text, "no affordance at all"
    assert '#mdExtras .mod[data-open="true"] .chev{transform:rotate(90deg)' in text, \
        "the chevron does not turn, so it says nothing about the state"

    build = text[text.index("function buildOptions("):]
    build = build[:build.index("\n    const paint = ")]
    assert 'chev.textContent' in build
    assert "Click to choose what to install" in build, \
        "nothing says what opening the row gets you"


def test_the_closed_row_still_says_what_is_going_on():
    """
    collapsing it must not mean losing the state. Somebody who closed the row
    after dropping the mod should still see that it is waiting on them.
    """
    text = ui()
    paint = text[text.index("const paint = () => {"):]
    paint = paint[:paint.index("\n    };")]

    assert "row.dataset.ready" in paint
    assert "selected, click to change" in paint
    assert '[data-ready="true"][data-open="false"]' in text, \
        "a mod is dropped and the row waiting to be opened looks like every other row"


def test_the_waiting_rows_bar_stands_clear_of_its_name():
    """
    an inset bar on the row's own edge sat on the first letter of the name and
    of both lines under it. Out in the drawer's margin it marks the row without
    touching any of it
    """
    text = ui()
    ready = '#mdExtras .mod.has-opts[data-ready="true"][data-open="false"]'
    rules = re.findall(re.escape(ready) + r"(::before)?\{([^}]*)\}", text)
    assert rules, "the waiting row has no marker"
    assert not any("inset" in body for _, body in rules), "the bar is back on the row's edge"
    bar = next(body for pseudo, body in rules if pseudo)
    left = re.search(r"left:(-?\d+)px", bar)
    width = re.search(r"width:(\d+)px", bar)
    assert left and width and int(left.group(1)) + int(width.group(1)) < 0, \
        "the bar reaches the row, so it touches the name"
    # in the drawer's own padding, so the scroller doesn't clip it
    pad = re.search(r"#mdExtras\{[^}]*padding:\d+px (\d+)px", text)
    assert pad and -int(left.group(1)) <= int(pad.group(1))


def test_dropping_the_mod_opens_the_options_without_being_asked():
    """
    the drop is the moment somebody is choosing, so that is when the choices
    should be in front of them
    """
    text = ui()
    body = text[text.index("function showOptions("):]
    body = body[:body.index("\n  function readableSize(")]

    assert "open();" in body, "the drawer is not opened"
    assert 'row.dataset.open = "true"' in body, "the row is not expanded"

    # and NOT scrollIntoView, which walks up to the nearest scrollable
    # ancestor, which here is the app, and slides the whole stage sideways.
    # The comment saying so is allowed to name it; the code is not.
    code = "\n".join(line for line in body.splitlines() if not line.strip().startswith("//"))
    assert "scrollIntoView" not in code
    assert "reveal(row);" in code
    # the drawer's body is the one scroller. Moving the drawer, which can't
    # scroll, did nothing
    reveal = text[text.index("  function reveal(row){"):]
    reveal = reveal[:reveal.index("\n  }\n")]
    assert "scrollIntoView" not in reveal and 'const host = $("#mdBody");' in reveal
    assert "host.scrollTop = " in reveal


def test_each_category_says_what_it_costs():
    """
    Weapons is 77MB and Enemies is 486MB. Seven labels in a column say nothing
    about that, and it is the whole basis for choosing between them.
    """
    text = ui()
    assert "#mdExtras .opt-row .size{" in text

    rule = text[text.index("#mdExtras .opt-row .size{"):]
    rule = rule[:rule.index("}")]
    assert "tabular-nums" in rule, "seven rows of figures that do not line up"

    build = text[text.index("function buildOptions("):]
    assert 'size.className = "size"' in build
    assert "function readableSize(" in text
    assert "function sizeUp(" in text


def test_the_sizes_survive_the_rows_being_rebuilt():
    """
    refresh() rebuilds the panel, and the first cut wrote the numbers straight
    onto the rows, so they vanished the moment anything was ticked
    """
    text = ui()
    assert "let catSizes = null;" in text

    paint = text[text.index("const paint = () => {"):]
    paint = paint[:paint.index("\n    };")]
    assert "catSizes" in paint, "paint does not reapply them, so a repaint loses them"

    grab = text[text.index("function sizeUp("):]
    grab = grab[:grab.index("\n  function ") if "\n  function " in grab else len(grab)]
    assert "catSizes =" in grab and "repaint()" in grab


def test_a_size_that_is_not_known_renders_as_nothing():
    """
    a zero beside a category reads as "free", which is worse than saying
    nothing. Zones is the live case: its entries are markers against a
    different archive set and it has no chr models at all.
    """
    from patcher.mods.hd_preflight import category_sizes

    text = ui()
    paint = text[text.index("const paint = () => {"):]
    assert 'bytes ? readableSize(bytes) : ""' in paint

    assert "if total:" in (REPO / "patcher" / "mods" / "hd_preflight.py").read_text(encoding="utf-8"), \
        "a category that resolved nothing is reported as zero rather than omitted"


def test_the_survey_never_takes_the_panel_down():
    """
    numbers and a badge beside labels. Nothing here is worth failing a drop
    over, and every early return has to hand back the same shape or the page
    reads a missing key as "not applied".
    """
    source = (REPO / "patcher" / "bridge.py").read_text(encoding="utf-8")
    body = source[source.index("def hd_survey("):source.index("def set_hd_categories(")]

    assert body.count("return answer") >= 4, "some path can still raise"
    assert "except Exception:" in body
    assert '"verified": False' in body, "an early return omits verified, which reads as False"


def test_sizes_come_from_the_index_and_the_mods_own_lists(tmp_path):
    """
    both halves are already on the machine: the game's index has the size of
    every model and Data\\LoadHD*.txt says which ids a category covers
    """
    from patcher.archive import filelist as fl
    from patcher.archive.entry import ChunkEntry, format_chunk
    from patcher.mods.hd_preflight import category_sizes

    entries = [
        ChunkEntry(path="chr/weapon/w001/bin/w001.win32.trb", sector_offset=0,
                   uncompressed=1000, stored=500),
        ChunkEntry(path="chr/weapon/w001/bin/w001.win32.imgb", sector_offset=1,
                   uncompressed=3000, stored=1500),
        ChunkEntry(path="chr/npc/n910/bin/n910.win32.trb", sector_offset=2,
                   uncompressed=70, stored=70),
    ]
    made = fl.Filelist(header=b"\x00" * 32, entry_records=b"", chunks=[format_chunk(entries)],
                       original_blobs=[None], edited={0}, entry_count=len(entries))
    book = tmp_path / "filelist_scru.win32.bin"
    fl.save(book, made)

    data = tmp_path / "Data"
    data.mkdir()
    (data / "LoadHDWeapons.txt").write_text("#:Weapons\nw w001\n", encoding="utf-8")
    (data / "LoadHDNPCs.txt").write_text("#:NPCs\nn n910\n", encoding="utf-8")

    sizes = category_sizes(book, data, [
        {"id": "weapons", "list": "LoadHDWeapons.txt"},
        {"id": "npcs", "list": "LoadHDNPCs.txt"},
        {"id": "zones", "list": "LoadHDEnvironments.txt"},
    ])

    assert sizes["weapons"] == 4000, "the trb and the imgb are both the model"
    assert sizes["npcs"] == 70
    assert "zones" not in sizes, "a list that is not there came back as zero"


def test_a_category_already_in_the_game_says_so_instead_of_a_size():
    """
    what it costs stops being the question once it is already there, and
    somebody about to press a forty minute button wants to know which of the
    seven are done
    """
    text = ui()
    assert "#mdExtras .opt-row .size.on{" in text
    rule = text[text.index("#mdExtras .opt-row .size.on{"):]
    rule = rule[:rule.index("}")]
    assert "var(--gold)" in rule, "the badge is the same grey as a size"
    assert "animation:" in rule, "it arrives after the row, so it should arrive visibly"

    paint = text[text.index("const paint = () => {"):]
    paint = paint[:paint.index("\n    };")]
    assert '"APPLIED"' in paint
    assert 'el.dataset.applied' in paint, "only the text changes, so a column of seven does not read"


def test_what_the_game_says_beats_what_the_record_remembers():
    """
    the record goes stale after Steam verifies the files, after a manual
    restore, on a game copied from another machine. A badge that says APPLIED
    has to be true or it is worse than no badge.
    """
    text = ui()
    paint = text[text.index("const paint = () => {"):]
    paint = paint[:paint.index("\n    };")]

    assert "const here = inGame || installed;" in paint, \
        "the record is used even when the archive was actually inspected"

    grab = text[text.index("function sizeUp("):]
    grab = grab[:grab.index("\n  return {")]
    assert "answer.verified" in grab, \
        "an unverified answer is treated as if it had said nothing is installed"


def test_the_button_is_the_only_thing_under_the_toggles():
    """
    a sentence under Install saying "Installs 4." restated the button, which
    says Install (4), directly above a column of four ticked boxes. Three
    places for one number.
    """
    text = ui()
    opts = text[text.index("function buildOptions("):text.index("\n  function refresh(")]

    assert "install.textContent = running" in opts
    assert 'className = "hint"' not in opts, "the sentence under the button is back"
    assert "#mdExtras .opts .hint{" not in text, "and its rule with it"

    # and the row it sits in holds one control
    assert "actions.appendChild(install);" in opts


def test_there_is_no_uninstall_control_in_the_panel():
    """
    Deliberately dropped: what it did was rebuild the whole archive from the
    vanilla backup, which is a full run to take one category off. install()
    still has the mode that does it, and restore_hd_project is still on the
    Api, so the capability is there for a caller that wants it.
    """
    text = ui()
    opts = text[text.index("function buildOptions("):text.index("\n  function refresh(")]

    for gone in ("Uninstall", "doomed", "armed", '"mode"', "Press again"):
        assert gone not in opts, f"the remove pass left {gone!r} behind"
    for gone in ("#mdExtras .opts .mode{", "#mdExtras .opts .run.bad",
                 "@keyframes runArmed", '[data-doomed="true"]'):
        assert gone not in text, f"dead rule: {gone}"

    # the backend half is untouched, because it is what makes adding cheap
    shell = (REPO / "shell.py").read_text(encoding="utf-8")
    assert 'mode: str = "add"' in shell and 'if mode == "set":' in shell


def test_an_applied_category_can_be_ticked_to_repair_it():
    """
    it used to stop dead here, and that made a bad install unrecoverable from
    the panel: a corrupted category could not be rebuilt, so the only way
    forward was Revert and an hour spent on six categories that were fine.

    It goes through now because every HD file is derived from the vanilla one
    in models\\orig rather than copied out of the mod, so building a category
    again writes the same bytes whatever state the game is in.
    """
    text = ui()
    flip = text[text.index("      const flip = e => {"):]
    flip = flip[:flip.index("\n      };")]

    assert "blocked.has(category.id)" in flip, "the unsupported guard is gone"
    assert "(inGame || installed).has(category.id)) return" not in flip, \
        "an applied category still cannot be ticked, so a broken one cannot be fixed"
    assert "touched.add(category.id)" in flip, \
        "a deliberate tick is not marked, so the next repaint undoes it"

    # only the unsupported ones are inert now
    paint = text[text.index("const paint = () => {"):]
    paint = paint[:paint.index("\n    };")]
    assert 'const locked = el.dataset.blocked === "true";' in paint, \
        "applied is locking the row again"


def test_the_record_cannot_put_a_tick_back_on_an_installed_category():
    """
    the live case, and the one that made this worth doing. hd_categories reads
    back what ran as `selected`, so reopening the drawer restored a tick on
    every category already in the game.

    Still true with repair in: an applied category is untickable by accident
    and repairable on purpose, and only a real click counts as on purpose.
    """
    text = ui()
    paint = text[text.index("const paint = () => {"):]
    paint = paint[:paint.index("\n    };")]

    assert ("for (const id of [...chosen]) "
            "if (here.has(id) && !touched.has(id)) chosen.delete(id);") in paint
    # what's in from the drawer, HD Models Plus for the characters, is a badge
    # and nothing else: a recorded tick on it stays, and the voice box only
    # counts the HD Project's own. tools/mods_probe.js clicks it through
    assert "const shownIn = new Set([...here, ...Object.keys(covered)]);" in paint
    assert "voicesCost(here)" in paint

    # seeded from defaults, so a default that is already in the game would
    # otherwise arrive ticked and quietly queue a rebuild of it
    build = text[text.index("const touched = new Set();"):]
    assert "touched" in build[:40]


def test_a_queued_rebuild_says_so_rather_than_reading_as_an_install():
    """
    Install (1) against a row that already says APPLIED is the same sentence
    twice, and the one thing somebody needs to know before pressing it is that
    this run is going to rebuild rather than add.

    Reinstall and not Repair, because Repair is a diagnosis and a button
    cannot make one. The reasons to rebuild are "it looked wrong in game", "a
    newer build fixed something", "a run died partway", and Reinstall is true
    in all of them.
    """
    text = ui()
    paint = text[text.index("const paint = () => {"):]
    paint = paint[:paint.index("\n    };")]

    assert '"REINSTALL"' in paint, "a queued rebuild still shows its size or APPLIED"
    assert '"REPAIR"' not in paint, "Repair claims a diagnosis the button has not made"
    assert 'fixes && fixes === chosen.size ? "Reinstall" : "Install"' in paint, \
        "the button says Install about a run that only rebuilds"
    # its own colour: gold already means "this is in the game", and a rebuild
    # queued against it is the opposite claim
    assert "#mdExtras .opt-row .size.fix{" in text


def test_a_category_the_survey_found_wrong_says_so_on_its_own():
    """
    the answer to "how would a user know". Before this the only way to learn a
    category had installed badly was to play the game, see it, quit and come
    back, and the tool had the evidence to say so the whole time.

    A finding, not a verb: NEEDS REBUILD is a statement about what was read off
    the disk, which is a claim the tool has actually earned, unlike Repair.
    """
    text = ui()
    paint = text[text.index("const paint = () => {"):]
    paint = paint[:paint.index("\n    };")]

    assert "suspect[id]" in paint, "the survey's finding never reaches the row"
    assert '"NEEDS REBUILD"' in paint
    assert 'el.dataset.wrong = wrong ? "true" : "false"' in paint

    # the reason travels with it, because a badge nobody can explain is worse
    # than no badge. Appended to the caveat rather than replacing it: they are
    # different sentences and the survey lands after the row is built.
    assert "mark.dataset.warn" in paint and 'join("\\n\\n")' in paint
    assert "white-space:pre-line" in text, "the two sentences run together"

    # amber, not gold and not the rebuild blue: this is the one thing on the
    # row that should catch an eye
    assert "#mdExtras .opt-row .size.bad{" in text
    assert '#mdExtras .opt-row[data-wrong="true"] .why{' in text

    # and only about something the HD Project actually put in the game
    assert "const wrong = here.has(id) ? suspect[id] : \"\";" in paint


def test_a_categorys_caveat_is_on_demand_rather_than_in_the_way():
    """
    two of the seven ship a paragraph of warning and they rendered under the
    row, so a list of toggles read as prose with checkboxes in it. The text is
    worth keeping and worth having to ask for.
    """
    text = ui()
    build = text[text.index("function buildOptions("):text.index("\n  function refresh(")]

    assert 'mark.className = "why"' in build
    assert 'mark.textContent = "i"' in build
    assert 'bubble.className = "tip"' in build
    assert "category.warn" in build

    # not a title attribute: the native tooltip is slow, self dismissing,
    # unstyleable and invisible to the keyboard
    assert ".title = " not in build and 'setAttribute("title"' not in build
    assert "mark.tabIndex = 0" in build
    assert 'mark.setAttribute("aria-label", category.warn)' in build

    # and the old paragraph is gone rather than hidden
    assert 'why.textContent = category.warn' not in build
    assert "#mdExtras .opts .why{" not in text


def test_reading_a_caveat_does_not_tick_the_box():
    """the whole row is the toggle and the marker sits inside it"""
    build = ui()
    build = build[build.index("function buildOptions("):build.index("\n  function refresh(")]
    assert 'mark.addEventListener("click", e => e.stopPropagation())' in build


def test_the_caveat_bubble_stays_inside_the_drawer():
    """
    .t ellipsises a long name, so a bubble parented to it would be clipped to a
    sliver. Anchored to the 13px marker instead it ran off the left edge of a
    400px drawer, and any fixed width is one drawer width from doing it again.
    """
    text = ui()
    build = text[text.index("function buildOptions("):text.index("\n  function refresh(")]
    assert "item.appendChild(mark);" in build
    assert "text.appendChild(mark)" not in build

    row = text[text.index("#mdExtras .opt-row{"):]
    row = row[:row.index("}")]
    assert "position:relative" in row, "the bubble has nothing to span"

    tip = text[text.index("#mdExtras .opt-row .why .tip{"):]
    tip = tip[:tip.index("}")]
    assert "left:0" in tip and "right:0" in tip, \
        "a bubble anchored to the marker overflows whichever edge the marker is near"
    assert "width:" not in tip, "a fixed width is one drawer width from overflowing"

    # the first row's bubble would go off the top of the list
    assert "#mdExtras .opt-row:first-of-type .why .tip{" in text


def test_the_toggles_are_not_fenced_off_by_a_rule_down_their_side():
    """
    every row carried a 1px left border, and seven of them stacked into one
    unbroken line beside the mod's name. The indent already nests them.
    """
    text = ui()
    rule = text[text.index("#mdExtras .opt-row{position:relative"):]
    rule = rule[:rule.index("}")]

    assert "border-left" not in rule, "the line down the side of the list is back"
    assert "padding:5px 0 5px 12px" in rule, "and now they do not read as nested at all"


def test_the_drawer_says_when_it_is_still_loading():
    """
    "Found FFXIII-2 HD. Pick what you want below." went up the moment the
    folder was recognised, while the game was still being read, and with the
    overlay stuck too it looked like the app had locked up. The patch queue's
    disc says "working" everywhere else, so it says it here, left of Close.
    """
    text = ui()
    foot = text[text.index("      <footer>\n        <div id=\"mdNote\""):]
    foot = foot[:foot.index("</footer>")]
    assert foot.index('id="mdDisc"') < foot.index('id="mdClose"'), \
        "the disc isn't beside Close"

    assert '#md footer.loading #mdDisc{' in text
    rule = text[text.index("#mdDisc{"):]
    assert 'disc.png' in rule[:rule.index("}")]
    spinning = text[text.index("#md footer.loading #mdDisc{"):]
    assert "animation:spin" in spinning[:spinning.index("}")]

    body = text[text.index("  function showOptions(found){"):]
    body = body[:body.index("\n  function readableSize(")]
    # what it's doing, not one "Loading up" for the whole wait
    assert 'const mine = working("Checking your game for", name, "", "survey");' in body
    assert "Loading up" not in text
    assert "done(`${name} is ready. Pick what you want below.`)" in body
    assert "Found ${detail.name}" not in body, "it claims to be ready before the read answers"


def test_anything_that_says_something_stops_the_disc():
    """a finished job that forgot to stop it would leave it spinning on nothing"""
    text = ui()
    body = text[text.index("  function note(text, detail){"):]
    body = body[:body.index("\n  }")]
    assert "spin(false)" in body
    # done and fail say it through note, so they stop it too
    for name in ("done(text, detail)", "fail(text, detail)"):
        said = text[text.index(f"  function {name}{{"):]
        assert "note(" in said[:said.index("\n  }")], name

    route = text[text.index("  async function route(path, onlyHd, tries = 0, game = null){"):]
    route = route[:route.index("\n  function explain(")]
    assert "api.unpack_drop(path))" in route and "r.ok !== false" in route, \
        "an unpack the backend refused still spins forever"
    # and one turned away with nothing of ours unpacking has no answer coming
    assert "if (going) unpacking = false;" in route


def test_install_waits_for_the_read_of_the_game():
    """
    before the survey answers, an applied category still carries its default
    tick, and pressing Install then queues an hour of rebuilding
    """
    text = ui()
    paint = text[text.index("const paint = () => {"):]
    paint = paint[:paint.index("\n    };")]
    assert "surveying > 0" in paint

    grab = text[text.index("  function sizeUp(found){"):]
    grab = grab[:grab.index("\n  function resurvey(")]
    assert "Promise.race([asked, sleep(SURVEY_WAIT_MS)])" in grab, \
        "a read that never answers holds Install for good"


def test_install_asks_for_the_required_fixes():
    """
    XIII-2 refuses HD without the 4GB Patch, so the run puts the required fixes
    on first, and the rows hear what went in without reading the archives the
    install is busy rewriting
    """
    text = ui()
    start = text[text.index("  function start(game, chosen, redraw, how, voices){"):]
    start = start[:start.index("\n  function stop(")]
    assert 'jobsOf(owner, "required")' in start
    assert "install_hd_project(game, source.detail.root, chosen, way, fixes,\n" in start

    receive = text[text.index("  function receive(ev){"):]
    receive = receive[:receive.index('if (ev.stage === "done")')]
    assert 'ev.stage === "patched"' in receive
    assert "applied.set(ev.gameId" in receive and "buildGauge()" in receive


def test_any_drop_goes_to_one_door_that_works_out_what_it_is():
    """a folder that isn't the HD Project used to be told it wasn't, and nothing else"""
    text = ui()
    route = text[text.index("async function route("):]
    route = route[:route.index("\n  }\n")]
    assert '["archive", "file", "folder"].includes(what.kind)' in route
    # and on the game it was asked for on, an Open page download's included
    assert "return accept(path, { title: what.title, game: game || target, queued: !!game });" \
        in route

    accept = text[text.index("function accept(path, pick)"):]
    accept = accept[:accept.index("\n  }\n")]
    # on the game it was dropped on, whichever is up when it answers
    assert "const game = (pick && pick.game) || target;" in accept
    assert "api.install_mod(game, path, origin, version)" in accept
    assert 'kind === "hdproject"' in accept and "showOptions(result)" in accept
    assert 'kind === "versions"' in accept and "versions(path, origin, result, game)" in accept


def test_a_mod_with_versions_asks_which_as_one_choice():
    """three rows with an Install each read as three more mods"""
    text = ui()
    body = text[text.index("function versions(path, origin, result, game, quiet)"):]
    body = body[:body.index("\n  }\n")]
    # the recommended one starts picked, anything else waits on a pick
    assert "pick: all.includes(result.recommended) ? result.recommended : null" in body
    choice = text[text.index("  function buildChoice(row, label, ask){"):]
    choice = choice[:choice.index("\n  }\n")]
    # on the game it was dropped on, whichever is up when it's picked, and
    # answered once it's in: turned away, it's still up to press again
    assert "accept(ask.path, {origin: ask.origin, version: ask.pick, game: ask.game}).then(result => {" in choice
    assert "if (result && (result.ok || result.kind)) asking.delete(ask.game);" in choice
    assert 'item.setAttribute("role", "radio")' in choice
    assert '`Install ${name(ask.pick)}`' in choice
    # one question a game, under its own Worth getting row when it has one, or Dropped
    assert "const asking = new Map();" in text
    assert 'const head = heading("Dropped");' in text
    row = text[text.index("  function extraRow(job, ask, owner){"):]
    row = row[:row.index("\n  }\n")]
    assert "else if (job === owner) buildChoice(row, label, ask);" in row
    # and redrawn on its own, so the HD Project's options open beside it stay so
    body = text[text.index("function versions(path, origin, result, game, quiet)"):]
    body = body[:body.index("\n  }\n")]
    assert "redrawAsk();" in body and "renderExtras()" not in body


def test_a_big_mod_says_how_far_it_has_got():
    text = ui()
    assert 'event.type === "mods"){ Mods.progress(event); return; }' in text
    assert "progress," in text[text.index("return { offer, open, close, isOpen, accept"):]
    body = text[text.index("  function progress(ev){"):]
    body = body[:body.index("\n  }\n")]
    # nothing lands after the result to put the disc back on over it
    assert 'if (who === "unpack" ? !unpacking : !busyWith) return;' in body
    # a percent of the step whatever it counts in, however few there are, and
    # what's out so far for an unpack that won't say what it holds
    assert "Math.floor(ev.done * 100 / ev.of)" in body and "ev.of >= 50" not in body
    assert ": ev.bytes && ev.done >= 1048576 ? readableSize(ev.done) : \"\";" in body
    # and a step with a count ticks over in place, on its own job's line only
    step = text[text.index("  function step(who, verb, name, count){"):]
    step = step[:step.index("\n  }\n")]
    # held for later rather than dropped, for a job with no more steps to say
    assert "if (!canSay(who)){ muted.delete(who); muted.set(who, [verb, name, count]); later(); return; }" \
        in step
    assert 'there.textContent = count ? ` ${count}` : "";' in step


def test_remove_says_which_kind_of_mod_it_is():
    """a game-file mod and an add-on folder can share a name"""
    text = ui()
    assert "api.remove_addon(target, mod.name, mod.kind || null)" in text


def test_several_files_dropped_at_once_all_go_in():
    """a model's loose .trb and .imgb are two files, and only the first went in"""
    text = ui()
    dropped = text[text.index("async function dropped(paths, detail)"):]
    dropped = dropped[:dropped.index("\n  }\n")]
    assert "await Mods.route(path);" in dropped
    assert "for (const more of (paths || []).slice(1).filter(Boolean)) await Mods.route(more);" \
        in dropped
