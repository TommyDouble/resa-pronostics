"""Contrat HTML de /admin/resultats : le gabarit mobile ne doit pas casser les
sélecteurs/attributs dont dépendent resa.js (initResultForms) et la macro
encode_form de dashboard.html.

On parse le HTML (au lieu de simples recherches de sous-chaînes) pour vérifier
séparément le formulaire d'encodage principal, le formulaire de correction et
son <details>/<summary>, afin que les assertions portent bien sur le bon
élément et pas sur une occurrence fortuite ailleurs dans la page.
"""
from html.parser import HTMLParser

from app.database import get_db
from tests.conftest import run

# Numéros de match et date volontairement hors de toute plage utilisée par les
# autres fichiers de tests (900000-999999 notamment, cf. tests/test_trophies.py)
# pour garantir l'absence de collision de match_number.
_PENDING_NUMBER = 5_000_001
_DONE_NUMBER = 5_000_002
# Date la plus lointaine possible : garantit que ce match reste le plus récent
# (ORDER BY match_date DESC LIMIT 10) quelle que soit la pollution laissée par
# d'autres tests dans la base de test partagée (scope="session").
_DONE_DATE = "9999-12-31"
_ZERO_ZERO_NUMBER = 5_000_003

_INTERACTIVE_TAGS = {"input", "select", "textarea", "button"}
_VOID_ELEMENTS = {
    "input", "br", "img", "hr", "meta", "link", "col", "source", "area", "base",
    "embed", "track", "wbr",
}


class _Node:
    def __init__(self, tag, attrs):
        self.tag = tag
        self.attrs = dict(attrs)
        self.children = []
        self.parent = None

    def class_list(self):
        return (self.attrs.get("class") or "").split()

    def has_attr(self, name):
        return name in self.attrs

    def iter_descendants(self):
        for child in self.children:
            yield child
            yield from child.iter_descendants()

    def iter_ancestors(self):
        node = self.parent
        while node is not None:
            yield node
            node = node.parent


class _TreeBuilder(HTMLParser):
    """Construit un arbre DOM minimal ; suffisant pour du HTML bien formé
    généré par nos propres templates Jinja (pas un parseur HTML tolérant)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("root", {})
        self._stack = [self.root]

    def _append(self, tag, attrs):
        node = _Node(tag, attrs)
        node.parent = self._stack[-1]
        self._stack[-1].children.append(node)
        return node

    def handle_starttag(self, tag, attrs):
        node = self._append(tag, attrs)
        if tag not in _VOID_ELEMENTS:
            self._stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self._append(tag, attrs)

    def handle_endtag(self, tag):
        for i in range(len(self._stack) - 1, 0, -1):
            if self._stack[i].tag == tag:
                del self._stack[i:]
                break


def _parse(html):
    builder = _TreeBuilder()
    builder.feed(html)
    return builder.root


def _find_all(node, predicate):
    return [n for n in node.iter_descendants() if predicate(n)]


def _find_one(node, predicate, label):
    matches = _find_all(node, predicate)
    assert len(matches) >= 1, f"Aucun élément trouvé : {label}"
    return matches[0]


def _seed_pending_knockout_match(number):
    async def _create():
        async with get_db() as db:
            cursor = await db.execute(
                """INSERT INTO matches (match_number, phase, match_date, kickoff_time,
                                        team1_name, team2_name, weight)
                   VALUES (?, 'round_of_16', '2020-01-01', '10:00', 'Espagne', 'Allemagne', 2)""",
                (number,),
            )
            await db.commit()
            return cursor.lastrowid

    return run(_create())


def _seed_done_knockout_match(number):
    async def _create():
        async with get_db() as db:
            cursor = await db.execute(
                """INSERT INTO matches (match_number, phase, match_date, kickoff_time,
                                        team1_name, team2_name, weight,
                                        score_team1, score_team2, result, qualifier_winner)
                   VALUES (?, 'round_of_16', ?, '20:00', 'Portugal', 'Italie', 2,
                           1, 1, 'team1', 'team1')""",
                (number, _DONE_DATE),
            )
            await db.commit()
            return cursor.lastrowid

    return run(_create())


def _seed_done_zero_zero_ko_match(number):
    async def _create():
        async with get_db() as db:
            cursor = await db.execute(
                """INSERT INTO matches (match_number, phase, match_date, kickoff_time,
                                        team1_name, team2_name, weight,
                                        score_team1, score_team2, result, qualifier_winner)
                   VALUES (?, 'round_of_16', ?, '20:00', 'Argentine', 'Croatie', 2,
                           0, 0, 'team1', 'team1')""",
                (number, _DONE_DATE),
            )
            await db.commit()
            return cursor.lastrowid

    return run(_create())


def _cleanup_matches(numbers):
    async def _clean():
        async with get_db() as db:
            placeholders = ",".join("?" for _ in numbers)
            await db.execute(
                f"DELETE FROM matches WHERE match_number IN ({placeholders})", numbers
            )
            await db.commit()

    run(_clean())


def test_results_page_html_contract(admin_client):
    pending_id = _seed_pending_knockout_match(_PENDING_NUMBER)
    done_id = _seed_done_knockout_match(_DONE_NUMBER)
    try:
        html = admin_client.get("/admin/resultats").text
        root = _parse(html)

        # ---- Formulaire d'encodage principal (match en attente) ----
        pending_action = f"/admin/resultats/{pending_id}"
        pending_form = _find_one(
            root,
            lambda n: n.tag == "form" and n.attrs.get("action") == pending_action,
            f'form d\'encodage action="{pending_action}"',
        )
        assert "result-form" in pending_form.class_list()
        assert pending_form.has_attr("data-phase")

        score_fields = {"score_team1", "score_team2", "final_score_team1", "final_score_team2"}
        found_names = {
            n.attrs.get("name")
            for n in _find_all(pending_form, lambda n: n.tag == "input" and n.attrs.get("name") in score_fields)
        }
        assert score_fields == found_names, f"Champs de score manquants : {score_fields - found_names}"

        score_inputs = _find_all(
            pending_form,
            lambda n: n.tag == "input" and n.attrs.get("name") in score_fields,
        )
        for inp in score_inputs:
            assert inp.attrs.get("inputmode") == "numeric", (
                f'inputmode="numeric" manquant sur name="{inp.attrs.get("name")}"'
            )

        qualifier_select = _find_one(
            pending_form,
            lambda n: n.tag == "select" and n.attrs.get("name") == "qualifier_winner",
            'select name="qualifier_winner" dans le formulaire principal',
        )
        assert qualifier_select is not None

        _find_one(
            pending_form,
            lambda n: n.has_attr("data-ko-final-fields"),
            "élément [data-ko-final-fields] dans le formulaire principal",
        )

        # ---- Formulaire de correction (résultat récent) + <details>/<summary> ----
        correct_action = f"/admin/resultats/{done_id}/correct"
        correction_form = _find_one(
            root,
            lambda n: n.tag == "form" and n.attrs.get("action") == correct_action,
            f'form de correction action="{correct_action}"',
        )
        assert "result-form" in correction_form.class_list()

        details_ancestors = [a for a in correction_form.iter_ancestors() if a.tag == "details"]
        assert details_ancestors, "Le formulaire de correction doit être dans le contenu d'un <details>"
        details_el = details_ancestors[0]

        summary_el = _find_one(
            details_el,
            lambda n: n.tag == "summary",
            "élément <summary> dans le <details> de correction",
        )
        # Le <summary> doit rester une simple accroche cliquable, sans input/select/
        # textarea/bouton interactif imbriqué (le formulaire de correction vit à
        # côté, dans le corps du <details>, pas dans le <summary>).
        interactive_in_summary = _find_all(summary_el, lambda n: n.tag in _INTERACTIVE_TAGS)
        assert not interactive_in_summary, (
            "Le <summary> ne doit contenir aucun input/select/textarea/button : "
            f"trouvé {[n.tag for n in interactive_in_summary]}"
        )

        # ---- Aucun pronostic sur ce match : pas de confirmation enrichie ----
        assert not correction_form.has_attr("data-impact-confirm"), (
            "Le formulaire de correction ne doit pas porter data-impact-confirm quand "
            "aucun pronostic n'existe pour ce match (pred_count = 0)"
        )
        # Le formulaire de correction est un .result-form : il ne doit jamais porter
        # le data-confirm générique (initFormConfirm interceptrait le submit en plus
        # du garde-fou KO 0-0 / impact géré par initResultForms -> double confirmation).
        assert not correction_form.has_attr("data-confirm")
        assert not correction_form.has_attr("data-confirm-title")
        assert not correction_form.has_attr("data-confirm-danger")
    finally:
        _cleanup_matches([_PENDING_NUMBER, _DONE_NUMBER])


def test_results_page_correction_confirm_with_predictions(admin_client, participant):
    done_id = _seed_done_knockout_match(_DONE_NUMBER)
    try:
        async def _seed_prediction():
            async with get_db() as db:
                await db.execute(
                    """INSERT INTO predictions (participant_id, match_id, prediction)
                       VALUES (?, ?, 'team1')""",
                    (participant["id"], done_id),
                )
                await db.commit()

        run(_seed_prediction())

        html = admin_client.get("/admin/resultats").text
        root = _parse(html)

        correct_action = f"/admin/resultats/{done_id}/correct"
        correction_form = _find_one(
            root,
            lambda n: n.tag == "form" and n.attrs.get("action") == correct_action,
            f'form de correction action="{correct_action}"',
        )

        assert correction_form.attrs.get("data-impact-confirm-title") == "Corriger ce résultat ?"
        assert correction_form.attrs.get("data-impact-confirm") == (
            "Cette correction recalculera les points de 1 participant(s) "
            "ayant pronostiqué ce match."
        )
        assert correction_form.has_attr("data-impact-confirm-danger")
        assert not correction_form.has_attr("data-confirm-strong"), (
            "Pas de retype pour une correction de résultat (re-corrigible, pas irréversible)"
        )
        # Ne doit pas non plus porter le data-confirm générique (cf. bloquant
        # KO 0-0 + impact : une seule confirmation, gérée par initResultForms()).
        assert not correction_form.has_attr("data-confirm")
        assert not correction_form.has_attr("data-confirm-title")
        assert not correction_form.has_attr("data-confirm-danger")
    finally:
        _cleanup_matches([_PENDING_NUMBER, _DONE_NUMBER])


def test_results_page_correction_form_zero_zero_ko_has_dedicated_impact_attrs(admin_client, participant):
    # Cas bloquant identifié en review : correction d'un match KO à 0-0 avec des
    # pronostics existants. Le formulaire porte à la fois le garde-fou KO 0-0
    # (data-phase, géré par initResultForms) et l'impact chiffré. Les deux
    # doivent être combinés en une seule confirmation JS, jamais via le
    # data-confirm générique (qui serait intercepté séparément par initFormConfirm).
    done_id = _seed_done_zero_zero_ko_match(_ZERO_ZERO_NUMBER)
    try:
        async def _seed_prediction():
            async with get_db() as db:
                await db.execute(
                    """INSERT INTO predictions (participant_id, match_id, prediction)
                       VALUES (?, ?, 'draw')""",
                    (participant["id"], done_id),
                )
                await db.commit()

        run(_seed_prediction())

        html = admin_client.get("/admin/resultats").text
        root = _parse(html)

        correct_action = f"/admin/resultats/{done_id}/correct"
        correction_form = _find_one(
            root,
            lambda n: n.tag == "form" and n.attrs.get("action") == correct_action,
            f'form de correction action="{correct_action}"',
        )

        assert correction_form.attrs.get("data-phase") == "round_of_16"
        assert correction_form.attrs.get("data-impact-confirm-title") == "Corriger ce résultat ?"
        assert correction_form.attrs.get("data-impact-confirm") == (
            "Cette correction recalculera les points de 1 participant(s) "
            "ayant pronostiqué ce match."
        )
        assert correction_form.has_attr("data-impact-confirm-danger")

        # Pas de data-confirm générique : sinon initFormConfirm() interceptrait
        # aussi ce submit, en plus du garde-fou KO 0-0 d'initResultForms() ->
        # deux confirmations pour un seul clic.
        assert not correction_form.has_attr("data-confirm")
        assert not correction_form.has_attr("data-confirm-title")
        assert not correction_form.has_attr("data-confirm-danger")
        assert not correction_form.has_attr("data-confirm-strong")
    finally:
        _cleanup_matches([_ZERO_ZERO_NUMBER])
