"""Native Qt architecture diagrams for FastAPI Studio."""

from __future__ import annotations

from collections import defaultdict, deque

from qtpy.QtCore import QPointF, QRectF, Qt, Signal
from qtpy.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen, QPolygonF
from qtpy.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGraphicsPathItem,
    QGraphicsPolygonItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsTextItem,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from spyder_fastapi.core import (
    global_projection,
    impact_projection,
    impacted_routes,
    route_projection,
)
from spyder_fastapi.models import (
    DiagramNode,
    DiagramProjection,
    FastAPIMap,
    RouteTestIndex,
)


_NODE_ID = 0
_NODE_WIDTH = 220.0
_NODE_HEIGHT = 82.0
_X_GAP = 290.0
_Y_GAP = 118.0


class DiagramView(QGraphicsView):
    """Zoomable/pannable graph canvas with node activation signals."""

    sig_node_selected = Signal(str)
    sig_node_activated = Signal(str)

    def __init__(self, scene: QGraphicsScene, parent=None):
        super().__init__(scene, parent)
        self.setRenderHint(QPainter.Antialiasing, True)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)

    @staticmethod
    def _node_id(item) -> str | None:
        current = item
        while current is not None:
            node_id = current.data(_NODE_ID)
            if node_id:
                return str(node_id)
            current = current.parentItem()
        return None

    def mousePressEvent(self, event):
        node_id = self._node_id(self.itemAt(event.pos()))
        if node_id:
            self.sig_node_selected.emit(node_id)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        node_id = self._node_id(self.itemAt(event.pos()))
        if node_id:
            self.sig_node_activated.emit(node_id)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event):
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale(factor, factor)


class FastAPIDiagramWidget(QWidget):
    """Interactive projections of FastAPI contract/dependency lineage."""

    sig_open_source = Signal(str, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._api_map: FastAPIMap | None = None
        self._test_index = RouteTestIndex()
        self._projection: DiagramProjection | None = None
        self._node_items: dict[str, QGraphicsRectItem] = {}

        self._mode = QComboBox()
        self._mode.addItem("Route flow", "route")
        self._mode.addItem("Global architecture", "global")
        self._mode.addItem("Impact / blast radius", "impact")
        self._mode.currentIndexChanged.connect(self._mode_changed)

        self._focus_label = QLabel("Route")
        self._focus = QComboBox()
        self._focus.setMinimumContentsLength(28)
        self._focus.currentIndexChanged.connect(self._focus_changed)

        self._show_tests = QCheckBox("Tests")
        self._show_tests.setToolTip(
            "Overlay statically discovered route-to-test links."
        )
        self._show_tests.toggled.connect(self._render_current)

        self._fit = QPushButton("Fit")
        self._fit.clicked.connect(self.fit_to_view)
        self._zoom_in = QPushButton("+")
        self._zoom_in.setToolTip("Zoom in")
        self._zoom_in.clicked.connect(lambda: self._view.scale(1.2, 1.2))
        self._zoom_out = QPushButton("-")
        self._zoom_out.setToolTip("Zoom out")
        self._zoom_out.clicked.connect(lambda: self._view.scale(1 / 1.2, 1 / 1.2))
        self._reset_zoom = QPushButton("100%")
        self._reset_zoom.clicked.connect(self.reset_zoom)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("View"))
        controls.addWidget(self._mode)
        controls.addWidget(self._focus_label)
        controls.addWidget(self._focus, 1)
        controls.addWidget(self._show_tests)
        controls.addWidget(self._fit)
        controls.addWidget(self._zoom_out)
        controls.addWidget(self._reset_zoom)
        controls.addWidget(self._zoom_in)

        self._summary = QLabel("Inspect a FastAPI app to render its architecture.")
        self._summary.setWordWrap(True)

        self._scene = QGraphicsScene(self)
        self._view = DiagramView(self._scene)
        self._view.sig_node_selected.connect(self._node_selected)
        self._view.sig_node_activated.connect(self._node_activated)

        self._details = QPlainTextEdit()
        self._details.setReadOnly(True)
        self._details.setPlaceholderText(
            "Select a node to inspect its FastAPI lineage and source."
        )

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._view)
        splitter.addWidget(self._details)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 2)

        layout = QVBoxLayout(self)
        layout.addLayout(controls)
        layout.addWidget(self._summary)
        layout.addWidget(splitter, 1)

    # --- Public integration API
    # ------------------------------------------------------------------
    def set_api_map(self, api_map: FastAPIMap) -> None:
        self._api_map = api_map
        self._repopulate_focus()
        self._render_current()

    def set_test_index(self, index: RouteTestIndex) -> None:
        self._test_index = index
        self._show_tests.setToolTip(
            "Overlay statically discovered route-to-test links "
            f"({len(index.references)} linked call(s))."
        )
        self._render_current()

    def set_show_tests(self, visible: bool) -> None:
        self._show_tests.setChecked(bool(visible))

    def select_route(self, route_id: str) -> None:
        if self._api_map is None:
            return
        route_index = self._mode.findData("route")
        if route_index >= 0:
            self._mode.setCurrentIndex(route_index)
        focus_index = self._focus.findData(route_id)
        if focus_index >= 0:
            self._focus.setCurrentIndex(focus_index)

    def focus_node(self, node_id: str) -> None:
        if self._api_map is None:
            return
        impact_index = self._mode.findData("impact")
        if impact_index >= 0:
            self._mode.setCurrentIndex(impact_index)
        focus_index = self._focus.findData(node_id)
        if focus_index >= 0:
            self._focus.setCurrentIndex(focus_index)

    def fit_to_view(self) -> None:
        bounds = self._scene.itemsBoundingRect()
        if bounds.isEmpty():
            return
        self._view.fitInView(
            bounds.adjusted(-40, -40, 40, 40),
            Qt.KeepAspectRatio,
        )

    def reset_zoom(self) -> None:
        self._view.resetTransform()

    # --- Projection controls
    # ------------------------------------------------------------------
    def _mode_changed(self, _index: int) -> None:
        self._repopulate_focus()
        self._render_current()

    def _focus_changed(self, _index: int) -> None:
        self._render_current()

    def _repopulate_focus(self) -> None:
        api_map = self._api_map
        mode = self._mode.currentData()

        previous = self._focus.currentData()
        self._focus.blockSignals(True)
        self._focus.clear()

        if api_map is None:
            self._focus.setEnabled(False)
            self._focus_label.setText("Focus")
        elif mode == "route":
            self._focus_label.setText("Route")
            self._focus.setEnabled(True)
            for route in api_map.routes:
                self._focus.addItem(route.id, route.id)
        elif mode == "impact":
            self._focus_label.setText("Dependency / model")
            self._focus.setEnabled(True)
            candidates = [
                node
                for node in api_map.lineage.nodes
                if node.kind in {"dependency", "model"}
            ]
            candidates.sort(key=lambda node: (node.kind, node.label, node.id))
            for node in candidates:
                count = len(impacted_routes(api_map, node.id))
                self._focus.addItem(
                    f"{node.kind}: {node.label} [{count}]",
                    node.id,
                )
        else:
            self._focus_label.setText("All routes")
            self._focus.setEnabled(False)

        if previous is not None:
            previous_index = self._focus.findData(previous)
            if previous_index >= 0:
                self._focus.setCurrentIndex(previous_index)

        self._focus.blockSignals(False)

    def _current_projection(self) -> DiagramProjection | None:
        if self._api_map is None:
            return None

        mode = self._mode.currentData()
        include_tests = self._show_tests.isChecked()
        if mode == "global":
            return global_projection(
                self._api_map,
                self._test_index,
                include_tests=include_tests,
            )

        focus = self._focus.currentData()
        if focus is None:
            return None

        if mode == "impact":
            return impact_projection(
                self._api_map,
                str(focus),
                self._test_index,
                include_tests=include_tests,
            )

        return route_projection(
            self._api_map,
            str(focus),
            self._test_index,
            include_tests=include_tests,
        )

    # --- Rendering
    # ------------------------------------------------------------------
    def _render_current(self) -> None:
        self._scene.clear()
        self._node_items.clear()
        self._details.clear()

        try:
            projection = self._current_projection()
        except KeyError as exc:
            self._projection = None
            self._summary.setText(str(exc))
            return

        self._projection = projection
        if projection is None:
            self._summary.setText(
                "Inspect a FastAPI app to render its architecture."
            )
            return

        positions = self._layout(projection)
        self._draw_edges(projection, positions)
        self._draw_nodes(projection, positions)

        self._summary.setText(
            f"{projection.title} - {len(projection.nodes)} nodes / "
            f"{len(projection.edges)} relationships"
        )
        self.fit_to_view()

        if projection.focus_id:
            self._node_selected(projection.focus_id)

    @staticmethod
    def _layout(projection: DiagramProjection) -> dict[str, QPointF]:
        nodes = {node.id: node for node in projection.nodes}
        adjacency: dict[str, list[str]] = defaultdict(list)
        indegree = {node_id: 0 for node_id in nodes}

        for edge in projection.edges:
            if edge.source not in nodes or edge.target not in nodes:
                continue
            adjacency[edge.source].append(edge.target)
            indegree[edge.target] = indegree.get(edge.target, 0) + 1

        roots = [root for root in projection.roots if root in nodes]
        if not roots:
            roots = sorted(
                node_id
                for node_id, count in indegree.items()
                if count == 0
            )
        if not roots and nodes:
            roots = [sorted(nodes)[0]]

        depth: dict[str, int] = {}
        queue = deque((root, 0) for root in roots)
        while queue:
            node_id, level = queue.popleft()
            if node_id in depth and depth[node_id] <= level:
                continue
            depth[node_id] = level
            for target in adjacency.get(node_id, []):
                queue.append((target, level + 1))

        fallback_depth = max(depth.values(), default=-1) + 1
        for node_id in nodes:
            depth.setdefault(node_id, fallback_depth)

        layers: dict[int, list[str]] = defaultdict(list)
        for node_id, level in depth.items():
            layers[level].append(node_id)

        positions: dict[str, QPointF] = {}
        for level in sorted(layers):
            node_ids = sorted(
                layers[level],
                key=lambda node_id: (
                    nodes[node_id].kind,
                    nodes[node_id].label,
                    node_id,
                ),
            )
            center = (len(node_ids) - 1) / 2
            for index, node_id in enumerate(node_ids):
                positions[node_id] = QPointF(
                    level * _X_GAP,
                    (index - center) * _Y_GAP,
                )

        return positions

    def _node_brush(self, node: DiagramNode) -> QBrush:
        palette = self.palette()
        if node.kind == "route":
            color = QColor(palette.highlight().color())
            color.setAlpha(70)
            return QBrush(color)
        if node.kind == "dependency":
            return QBrush(palette.alternateBase())
        if node.kind == "model":
            return QBrush(palette.midlight())
        if node.kind == "handler":
            return QBrush(palette.button())
        if node.kind == "test":
            return QBrush(palette.window())
        return QBrush(palette.base())

    @staticmethod
    def _node_text(node: DiagramNode) -> str:
        header = node.kind.upper()
        display_label = node.label
        if node.kind in {"dependency", "handler", "test"}:
            display_label = node.label.rsplit(".", 1)[-1]
        text = f"{header}\n{display_label}"
        if node.kind in {"dependency", "model"} and node.impact_count:
            suffix = "route" if node.impact_count == 1 else "routes"
            text += f"\nused by {node.impact_count} {suffix}"
        return text

    def _draw_nodes(
        self,
        projection: DiagramProjection,
        positions: dict[str, QPointF],
    ) -> None:
        palette = self.palette()
        normal_pen = QPen(palette.mid().color(), 1.4)
        focus_pen = QPen(palette.highlight().color(), 3.0)

        for node in projection.nodes:
            position = positions.get(node.id)
            if position is None:
                continue

            rect = QGraphicsRectItem(
                QRectF(
                    -_NODE_WIDTH / 2,
                    -_NODE_HEIGHT / 2,
                    _NODE_WIDTH,
                    _NODE_HEIGHT,
                )
            )
            rect.setPos(position)
            rect.setBrush(self._node_brush(node))
            rect.setPen(
                focus_pen
                if node.id == projection.focus_id
                else normal_pen
            )
            rect.setData(_NODE_ID, node.id)
            rect.setZValue(1)

            source_text = "source unavailable"
            if node.source is not None and node.source.file:
                source_text = node.source.file
                if node.source.line:
                    source_text += f":{node.source.line}"
            rect.setToolTip(
                f"{node.kind}: {node.label}\n{source_text}\n"
                "Double-click to open source when available."
            )
            self._scene.addItem(rect)
            self._node_items[node.id] = rect

            label = QGraphicsTextItem(rect)
            label.setPlainText(self._node_text(node))
            label.setDefaultTextColor(palette.text().color())
            label.setTextWidth(_NODE_WIDTH - 18)
            label.setData(_NODE_ID, node.id)
            bounds = label.boundingRect()
            label.setPos(
                -(_NODE_WIDTH - 18) / 2,
                -min(bounds.height(), _NODE_HEIGHT - 8) / 2,
            )

    def _draw_edges(
        self,
        projection: DiagramProjection,
        positions: dict[str, QPointF],
    ) -> None:
        palette = self.palette()
        pen = QPen(palette.mid().color(), 1.35)

        for edge in projection.edges:
            source = positions.get(edge.source)
            target = positions.get(edge.target)
            if source is None or target is None:
                continue

            start = QPointF(
                source.x() + _NODE_WIDTH / 2,
                source.y(),
            )
            end = QPointF(
                target.x() - _NODE_WIDTH / 2,
                target.y(),
            )
            delta = max(45.0, (end.x() - start.x()) / 2)

            path = QPainterPath(start)
            path.cubicTo(
                QPointF(start.x() + delta, start.y()),
                QPointF(end.x() - delta, end.y()),
                end,
            )
            path_item = QGraphicsPathItem(path)
            path_item.setPen(pen)
            path_item.setZValue(-2)
            path_item.setToolTip(edge.relation)
            self._scene.addItem(path_item)

            arrow = QGraphicsPolygonItem(
                QPolygonF(
                    [
                        end,
                        QPointF(end.x() - 11, end.y() - 5),
                        QPointF(end.x() - 11, end.y() + 5),
                    ]
                )
            )
            arrow.setPen(pen)
            arrow.setBrush(QBrush(palette.mid().color()))
            arrow.setZValue(-1)
            arrow.setToolTip(edge.relation)
            self._scene.addItem(arrow)

            if projection.mode != "global":
                relation = QGraphicsSimpleTextItem(edge.relation)
                relation.setBrush(QBrush(palette.text().color()))
                relation.setPos(
                    (start.x() + end.x()) / 2 - 30,
                    (start.y() + end.y()) / 2 - 12,
                )
                relation.setZValue(-1)
                self._scene.addItem(relation)

    # --- Selection/navigation
    # ------------------------------------------------------------------
    def _node_by_id(self, node_id: str) -> DiagramNode | None:
        if self._projection is None:
            return None
        return next(
            (node for node in self._projection.nodes if node.id == node_id),
            None,
        )

    def _node_selected(self, node_id: str) -> None:
        node = self._node_by_id(node_id)
        if node is None:
            return

        incoming: list[str] = []
        outgoing: list[str] = []
        if self._projection is not None:
            for edge in self._projection.edges:
                if edge.target == node_id:
                    incoming.append(f"{edge.relation} <- {edge.source}")
                if edge.source == node_id:
                    outgoing.append(f"{edge.relation} -> {edge.target}")

        source = "source unavailable"
        if node.source is not None and node.source.file:
            source = node.source.file
            if node.source.line:
                source += f":{node.source.line}"

        impacted = []
        if self._api_map is not None and node.kind in {"dependency", "model"}:
            impacted = impacted_routes(self._api_map, node.id)

        lines = [
            f"{node.kind.upper()}",
            node.label,
            "",
            f"Source: {source}",
        ]
        if impacted:
            lines.extend(
                [
                    "",
                    f"Blast radius: {len(impacted)} route(s)",
                    *[f"  {route}" for route in impacted],
                ]
            )
        if incoming:
            lines.extend(["", "Incoming", *[f"  {item}" for item in incoming]])
        if outgoing:
            lines.extend(["", "Outgoing", *[f"  {item}" for item in outgoing]])

        self._details.setPlainText("\n".join(lines))

    def _node_activated(self, node_id: str) -> None:
        node = self._node_by_id(node_id)
        if node is None or node.source is None or not node.source.file:
            return
        self.sig_open_source.emit(
            node.source.file,
            int(node.source.line or 1),
        )
