"""Convert a Flamapy FeatureModel to FAMA XML format and write it to disk.

Public API
----------
    fm_to_fama_xml(fm, output_path) -> list[str]
        Convert *fm* to FAMA XML, write to *output_path*, and return a list of
        warning strings for constraints that could not be expressed losslessly.

Supported cross-tree constraint shapes (all converted losslessly):
  - A REQUIRES B  (terminal → terminal)
  - A EXCLUDES B  (terminal → terminal)
  - NOT[AND[A][B]]             → A EXCLUDES B
  - EQUIVALENCE[A][B]          → A REQUIRES B + B REQUIRES A
  - IMPLIES[OR[A1..An]][B]     → Ai REQUIRES B  (all leaves terminals)
  - IMPLIES[A][AND[B1..Bn]]    → A REQUIRES Bi  (all leaves terminals)
  - AND[ctc1][ctc2]            → recurse into each branch

Any constraint that cannot be losslessly decomposed is skipped; the returned
list contains a human-readable description of each skipped constraint.
"""

import logging
from typing import Any
from xml.dom import minidom
import xml.etree.ElementTree as ET

from flamapy.core.models.ast import ASTOperation
from flamapy.core.transformations import ModelToText
from flamapy.metamodels.fm_metamodel.models import Feature, FeatureModel, Relation
from flamapy.metamodels.fm_metamodel.transformations.fm_secure_features_names import (
    FMSecureFeaturesNames,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def fm_to_fama_xml(fm: Any, output_path: str) -> list[str]:
    """Convert a flamapy FeatureModel to a FAMA XML file.

    Sanitises feature names, writes the XML to *output_path*, and returns
    warning messages for any constraints that were skipped.
    """
    sanitizer = FMSecureFeaturesNames(fm)
    safe_fm = sanitizer.transform()
    writer = _XMLWriter(output_path, safe_fm)
    writer.transform()
    return writer.skipped_constraints


# ---------------------------------------------------------------------------
# XMLWriter (internal)
# ---------------------------------------------------------------------------

class _XMLWriter(ModelToText):

    @staticmethod
    def get_destination_extension() -> str:
        return 'xml'

    def __init__(self, path: str, source_model: FeatureModel) -> None:
        self._path = path
        self._source_model = source_model
        self.skipped_constraints: list[str] = []

    def transform(self) -> str:
        self.skipped_constraints = []

        fm_el = ET.Element('feature-model')
        fm_el.set('xmlns:xsi', 'http://www.w3.org/2001/XMLSchema-instance')
        fm_el.set('xsi:noNamespaceSchemaLocation', 'feature-model-schema.xsd')

        root_feature = self._source_model.root
        feat_el = ET.SubElement(fm_el, 'feature')
        feat_el.set('name', root_feature.name)

        counters = [0, 0]  # [br_count, sr_count]
        _write_feature(root_feature, feat_el, counters)

        requires_list, excludes_list = self._extract_binary_constraints()

        for i, (feat_a, feat_b) in enumerate(requires_list, 1):
            req_el = ET.SubElement(fm_el, 'requires')
            req_el.set('name', f'Re-{i}')
            req_el.set('feature', feat_a)
            req_el.set('requires', feat_b)

        for i, (feat_a, feat_b) in enumerate(excludes_list, 1):
            exc_el = ET.SubElement(fm_el, 'excludes')
            exc_el.set('name', f'Ex-{i}')
            exc_el.set('feature', feat_a)
            exc_el.set('excludes', feat_b)

        rough_str = ET.tostring(fm_el, encoding='unicode')
        dom = minidom.parseString(rough_str)
        lines = dom.toprettyxml(indent='\t').splitlines()
        if lines and lines[0].startswith('<?xml'):
            lines[0] = '<?xml version="1.0" encoding="UTF-8"?>'
        xml_str = '\n'.join(lines)

        if self._path is not None:
            with open(self._path, 'w', encoding='utf-8') as fh:
                fh.write(xml_str)

        return xml_str

    def _extract_binary_constraints(
        self,
    ) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
        requires_list: list[tuple[str, str]] = []
        excludes_list: list[tuple[str, str]] = []
        for constraint in self._source_model.get_constraints():
            raw = str(constraint)
            reqs, excs, ok = _flatten_to_binary_pairs(constraint.ast.root)
            if ok:
                requires_list.extend(reqs)
                excludes_list.extend(excs)
            else:
                logger.warning('Constraint skipped (unsupported in XML format): %s', raw)
                self.skipped_constraints.append(raw)
        return requires_list, excludes_list


# ---------------------------------------------------------------------------
# Recursive constraint flattener
# ---------------------------------------------------------------------------

def _flatten_to_binary_pairs(
    node: object,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]], bool]:
    if node is None:
        return [], [], False

    op = getattr(node.data, 'value', str(node.data)).upper()
    left = node.left
    right = node.right

    is_req = (op in ('REQUIRES', 'IMPLIES', '=>') or node.data is ASTOperation.REQUIRES)
    is_exc = (op == 'EXCLUDES' or node.data is ASTOperation.EXCLUDES)

    if left is not None and right is not None and left.is_term() and right.is_term():
        if is_req:
            return [(str(left.data), str(right.data))], [], True
        if is_exc:
            return [], [(str(left.data), str(right.data))], True

    # NOT[AND[term][term]] → EXCLUDES
    if op == 'NOT' and left is not None and right is None:
        inner_op = getattr(left.data, 'value', str(left.data)).upper()
        if (inner_op == 'AND'
                and left.left is not None and left.right is not None
                and left.left.is_term() and left.right.is_term()):
            return [], [(str(left.left.data), str(left.right.data))], True

    # EQUIVALENCE[term][term] → A REQUIRES B + B REQUIRES A
    if op == 'EQUIVALENCE' and left is not None and right is not None:
        if left.is_term() and right.is_term():
            a, b = str(left.data), str(right.data)
            return [(a, b), (b, a)], [], True

    # AND[ctc1][ctc2] → recurse
    if op == 'AND' and left is not None and right is not None:
        r1, e1, ok1 = _flatten_to_binary_pairs(left)
        r2, e2, ok2 = _flatten_to_binary_pairs(right)
        if ok1 and ok2:
            return r1 + r2, e1 + e2, True
        return [], [], False

    # IMPLIES[OR-of-terminals][term] → Ai REQUIRES B
    if is_req and left is not None and right is not None and right.is_term():
        if getattr(left.data, 'value', str(left.data)).upper() == 'OR':
            antecedents = _collect_or_terminals(left)
            if antecedents is not None:
                return [(a, str(right.data)) for a in antecedents], [], True

    # IMPLIES[term][AND-of-terminals] → A REQUIRES Bi
    if is_req and left is not None and right is not None and left.is_term():
        if getattr(right.data, 'value', str(right.data)).upper() == 'AND':
            consequents = _collect_and_terminals(right)
            if consequents is not None:
                return [(str(left.data), c) for c in consequents], [], True

    return [], [], False


def _collect_or_terminals(node: object) -> list[str] | None:
    if node is None:
        return None
    if node.is_term():
        return [str(node.data)]
    if getattr(node.data, 'value', str(node.data)).upper() == 'OR':
        l = _collect_or_terminals(node.left)
        r = _collect_or_terminals(node.right)
        if l is not None and r is not None:
            return l + r
    return None


def _collect_and_terminals(node: object) -> list[str] | None:
    if node is None:
        return None
    if node.is_term():
        return [str(node.data)]
    if getattr(node.data, 'value', str(node.data)).upper() == 'AND':
        l = _collect_and_terminals(node.left)
        r = _collect_and_terminals(node.right)
        if l is not None and r is not None:
            return l + r
    return None


# ---------------------------------------------------------------------------
# Feature / relation serialisation helpers
# ---------------------------------------------------------------------------

def _write_feature(feature: Feature, parent_el: ET.Element, counters: list[int]) -> None:
    for relation in feature.get_relations():
        _write_relation(relation, parent_el, counters)


def _write_relation(relation: Relation, parent_el: ET.Element, counters: list[int]) -> None:
    children = list(relation.children)
    if len(children) == 1:
        counters[0] += 1
        br_el = ET.SubElement(parent_el, 'binaryRelation')
        br_el.set('name', f'BR-{counters[0]}')
        card_el = ET.SubElement(br_el, 'cardinality')
        card_el.set('min', str(relation.card_min))
        card_el.set('max', str(relation.card_max))
        child = children[0]
        sol_el = ET.SubElement(br_el, 'solitaryFeature')
        sol_el.set('name', child.name)
        _write_feature(child, sol_el, counters)
    elif len(children) > 1:
        counters[1] += 1
        sr_el = ET.SubElement(parent_el, 'setRelation')
        sr_el.set('name', f'SR-{counters[1]}')
        card_el = ET.SubElement(sr_el, 'cardinality')
        card_el.set('min', str(relation.card_min))
        card_el.set('max', str(relation.card_max))
        for child in children:
            gf_el = ET.SubElement(sr_el, 'groupedFeature')
            gf_el.set('name', child.name)
            _write_feature(child, gf_el, counters)
