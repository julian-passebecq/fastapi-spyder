"""Grafana-like native FastAPI telemetry dashboard for Spyder."""

from __future__ import annotations

from collections import defaultdict

from qtpy.QtCore import QPointF, QRectF, Qt, Signal
from qtpy.QtGui import QPainter, QPen, QPolygonF
from qtpy.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from spyder_fastapi.core.telemetry import (
    NativeTelemetryStore,
    span_category,
    span_target,
)
from spyder_fastapi.models import NativeTelemetrySpan


_ROLE_ROUTE_ID = 40
_ROLE_TRACE_ID = 41
_ROLE_FUNCTION = 42


def _ms(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f} ms"


class TelemetryTimeline(QWidget):
    """Small native Qt latency timeline; no plotting/browser dependency."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._samples: list[NativeTelemetrySpan] = []
        self.setMinimumHeight(120)
        self.setToolTip(
            "Recent FastAPI native HTTP server-span durations. "
            "X marks requests with 5xx/error telemetry."
        )

    def set_samples(self, samples: list[NativeTelemetrySpan]) -> None:
        self._samples = list(samples)
        self.update()

    @staticmethod
    def _failed(span: NativeTelemetrySpan) -> bool:
        raw_status = span.attributes.get("http.response.status_code")
        try:
            status = int(raw_status)
        except (TypeError, ValueError):
            status = 0
        return (
            status >= 500
            or span.status == "ERROR"
            or "error.type" in span.attributes
        )

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        palette = self.palette()
        bounds = QRectF(self.rect()).adjusted(44, 12, -12, -26)

        painter.setPen(QPen(palette.mid().color(), 1))
        painter.drawRect(bounds)

        samples = self._samples
        if not samples:
            painter.setPen(palette.text().color())
            painter.drawText(
                self.rect(),
                Qt.AlignCenter,
                "No native FastAPI server spans yet",
            )
            return

        max_ms = max(span.duration_ms for span in samples) or 1.0
        painter.setPen(palette.text().color())
        painter.drawText(
            4,
            18,
            f"{max_ms:.0f} ms",
        )
        painter.drawText(
            4,
            int(bounds.bottom()),
            "0",
        )
        painter.drawText(
            int(bounds.left()),
            self.height() - 6,
            f"recent {len(samples)} requests",
        )

        points: list[QPointF] = []
        denominator = max(1, len(samples) - 1)
        for index, span in enumerate(samples):
            x = bounds.left() + bounds.width() * index / denominator
            y = bounds.bottom() - (
                bounds.height() * span.duration_ms / max_ms
            )
            points.append(QPointF(x, y))

        painter.setPen(QPen(palette.highlight().color(), 2))
        if len(points) == 1:
            painter.drawEllipse(points[0], 2.5, 2.5)
        else:
            painter.drawPolyline(QPolygonF(points))

        error_pen = QPen(palette.text().color(), 2)
        for point, span in zip(points, samples):
            if not self._failed(span):
                continue
            painter.setPen(error_pen)
            painter.drawLine(
                QPointF(point.x() - 4, point.y() - 4),
                QPointF(point.x() + 4, point.y() + 4),
            )
            painter.drawLine(
                QPointF(point.x() - 4, point.y() + 4),
                QPointF(point.x() + 4, point.y() - 4),
            )


class TraceWaterfall(QWidget):
    """Native Qt span waterfall for one OpenTelemetry trace."""

    _ROW_HEIGHT = 28
    _TOP = 34

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[tuple[NativeTelemetrySpan, int]] = []
        self.setMinimumHeight(140)

    @staticmethod
    def _failed(span: NativeTelemetrySpan) -> bool:
        return (
            span.status == "ERROR"
            or "error.type" in span.attributes
        )

    @staticmethod
    def _ordered_rows(
        spans: list[NativeTelemetrySpan],
    ) -> list[tuple[NativeTelemetrySpan, int]]:
        if not spans:
            return []

        by_parent: dict[str | None, list[NativeTelemetrySpan]] = defaultdict(list)
        ids = {span.span_id for span in spans}
        for span in spans:
            by_parent[span.parent_span_id].append(span)

        roots = [
            span
            for span in spans
            if span.parent_span_id is None
            or span.parent_span_id not in ids
        ]
        roots.sort(
            key=lambda span: (
                0 if span.kind.upper() == "SERVER" else 1,
                span.start_ns,
                span.name,
            )
        )

        result: list[tuple[NativeTelemetrySpan, int]] = []
        seen: set[str] = set()

        def visit(span: NativeTelemetrySpan, depth: int) -> None:
            if span.span_id in seen:
                return
            seen.add(span.span_id)
            result.append((span, depth))
            children = sorted(
                by_parent.get(span.span_id, []),
                key=lambda child: (child.start_ns, child.end_ns, child.name),
            )
            for child in children:
                visit(child, depth + 1)

        for root in roots:
            visit(root, 0)

        for span in sorted(spans, key=lambda item: (item.start_ns, item.end_ns)):
            if span.span_id not in seen:
                visit(span, 0)

        return result

    def set_trace(self, spans: list[NativeTelemetrySpan]) -> None:
        self._rows = self._ordered_rows(list(spans))
        self.setMinimumHeight(
            max(140, self._TOP + len(self._rows) * self._ROW_HEIGHT + 28)
        )
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        palette = self.palette()

        if not self._rows:
            painter.setPen(palette.text().color())
            painter.drawText(
                self.rect(),
                Qt.AlignCenter,
                "Select a trace to render its native FastAPI spans",
            )
            return

        spans = [span for span, _depth in self._rows]
        start_ns = min(span.start_ns for span in spans)
        end_ns = max(span.end_ns for span in spans)
        total_ns = max(1, end_ns - start_ns)
        total_ms = total_ns / 1_000_000

        label_width = min(320.0, max(190.0, self.width() * 0.34))
        right_margin = 18.0
        timeline_left = label_width
        timeline_width = max(80.0, self.width() - label_width - right_margin)

        painter.setPen(QPen(palette.mid().color(), 1))
        for fraction in (0.0, 0.25, 0.5, 0.75, 1.0):
            x = timeline_left + timeline_width * fraction
            painter.drawLine(
                QPointF(x, self._TOP - 8),
                QPointF(
                    x,
                    self._TOP + len(self._rows) * self._ROW_HEIGHT,
                ),
            )
            painter.setPen(palette.text().color())
            painter.drawText(
                QRectF(x - 30, 4, 60, 20),
                Qt.AlignCenter,
                f"{total_ms * fraction:.1f}",
            )
            painter.setPen(QPen(palette.mid().color(), 1))

        painter.setPen(palette.text().color())
        painter.drawText(
            QRectF(timeline_left, 4, timeline_width, 20),
            Qt.AlignRight | Qt.AlignVCenter,
            "ms",
        )

        for index, (span, depth) in enumerate(self._rows):
            y = self._TOP + index * self._ROW_HEIGHT
            center_y = y + self._ROW_HEIGHT / 2

            function = span.attributes.get("code.function.name")
            target = span_target(span)
            category = span_category(span)
            label = span.name
            if function and span.name.startswith("fastapi."):
                label += f" · {str(function).rsplit('.', 1)[-1]}"
            elif target and category not in {"fastapi", "http-server"}:
                label += f" · {target}"
            label = ("  " * min(depth, 5)) + label

            painter.setPen(palette.text().color())
            painter.drawText(
                QRectF(6, y, label_width - 14, self._ROW_HEIGHT),
                Qt.AlignLeft | Qt.AlignVCenter,
                label,
            )

            x = timeline_left + (
                (span.start_ns - start_ns) / total_ns
            ) * timeline_width
            width = max(
                2.0,
                (max(0, span.end_ns - span.start_ns) / total_ns)
                * timeline_width,
            )
            bar = QRectF(
                x,
                center_y - 7,
                width,
                14,
            )

            brush = (
                palette.highlight()
                if span.kind.upper() == "SERVER"
                else palette.alternateBase()
            )
            painter.fillRect(bar, brush)
            painter.setPen(
                QPen(
                    palette.text().color()
                    if self._failed(span)
                    else palette.mid().color(),
                    2 if self._failed(span) else 1,
                )
            )
            painter.drawRect(bar)

            if width >= 52:
                painter.setPen(palette.text().color())
                painter.drawText(
                    bar.adjusted(4, -1, -4, 1),
                    Qt.AlignRight | Qt.AlignVCenter,
                    f"{span.duration_ms:.1f}",
                )


class _KpiBox(QGroupBox):
    def __init__(self, title: str, parent=None):
        super().__init__(title, parent)
        self._value = QLabel("-")
        self._value.setAlignment(Qt.AlignCenter)
        layout = QVBoxLayout(self)
        layout.addWidget(self._value)

    def set_value(self, value: str) -> None:
        self._value.setText(value)


class FastAPITelemetryWidget(QWidget):
    """Live dashboard over FastAPI's native OpenTelemetry traces/logs."""

    sig_clear = Signal()
    sig_route_selected = Signal(str)
    sig_function_selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._store = NativeTelemetryStore()

        header = QHBoxLayout()
        self._status = QLabel(
            "Native FastAPI telemetry: start a debug server to capture traces."
        )
        self._status.setWordWrap(True)
        header.addWidget(self._status, 1)

        self._clear = QPushButton("Clear telemetry")
        self._clear.clicked.connect(self.sig_clear.emit)
        header.addWidget(self._clear)

        kpis = QHBoxLayout()
        self._requests = _KpiBox("Requests")
        self._errors = _KpiBox("Errors")
        self._error_rate = _KpiBox("Error rate")
        self._validation = _KpiBox("Validation")
        self._exceptions = _KpiBox("Exceptions")
        self._external = _KpiBox("External spans")
        self._average = _KpiBox("Average")
        self._p50 = _KpiBox("P50")
        self._p95 = _KpiBox("P95")
        for widget in (
            self._requests,
            self._errors,
            self._error_rate,
            self._validation,
            self._exceptions,
            self._external,
            self._average,
            self._p50,
            self._p95,
        ):
            kpis.addWidget(widget)

        self._timeline = TelemetryTimeline()

        self._routes = QTreeWidget()
        self._routes.setRootIsDecorated(False)
        self._routes.setHeaderLabels(
            [
                "Route",
                "Requests",
                "Errors",
                "Error %",
                "Validation",
                "Average",
                "P50",
                "P95",
                "Last",
                "Status",
            ]
        )
        self._routes.itemDoubleClicked.connect(self._route_activated)

        self._traces = QTreeWidget()
        self._traces.setHeaderLabels(
            [
                "Trace / span",
                "Category",
                "Kind",
                "Duration",
                "Status",
                "Function / target",
            ]
        )
        self._traces.setRootIsDecorated(True)
        self._traces.currentItemChanged.connect(self._trace_selected)
        self._traces.itemDoubleClicked.connect(self._trace_activated)

        self._waterfall = TraceWaterfall()
        waterfall_scroll = QScrollArea()
        waterfall_scroll.setWidgetResizable(True)
        waterfall_scroll.setWidget(self._waterfall)

        trace_splitter = QSplitter(Qt.Vertical)
        trace_splitter.addWidget(self._traces)
        trace_splitter.addWidget(waterfall_scroll)
        trace_splitter.setStretchFactor(0, 3)
        trace_splitter.setStretchFactor(1, 2)

        trace_page = QWidget()
        trace_layout = QVBoxLayout(trace_page)
        trace_layout.setContentsMargins(0, 0, 0, 0)
        trace_layout.addWidget(trace_splitter)

        self._logs = QTreeWidget()
        self._logs.setRootIsDecorated(False)
        self._logs.setHeaderLabels(
            ["Severity", "Event", "Route", "Message", "Trace"]
        )

        self._tabs = QTabWidget()
        self._tabs.addTab(self._routes, "Routes")
        self._tabs.addTab(trace_page, "Traces / waterfall")
        self._tabs.addTab(self._logs, "FastAPI logs")

        layout = QVBoxLayout(self)
        layout.addLayout(header)
        layout.addLayout(kpis)
        layout.addWidget(self._timeline)
        layout.addWidget(self._tabs, 1)

        self.refresh()

    def set_store(self, store: NativeTelemetryStore) -> None:
        self._store = store
        self.refresh()

    def select_trace(self, trace_id: str) -> bool:
        """Focus one exact captured trace by OpenTelemetry trace id."""

        if not trace_id:
            return False

        for index in range(self._traces.topLevelItemCount()):
            item = self._traces.topLevelItem(index)
            if item.data(0, _ROLE_TRACE_ID) != trace_id:
                continue
            self._traces.setCurrentItem(item)
            item.setExpanded(True)
            self._traces.scrollToItem(item)
            self._tabs.setCurrentIndex(1)
            return True
        return False

    def select_route(self, route_id: str) -> bool:
        """Focus the latest captured trace for one FastAPI route."""

        route_item = None
        for index in range(self._routes.topLevelItemCount()):
            item = self._routes.topLevelItem(index)
            if item.data(0, _ROLE_ROUTE_ID) == route_id:
                route_item = item
                break

        if route_item is not None:
            self._routes.setCurrentItem(route_item)
            self._routes.scrollToItem(route_item)

        trace_item = None
        for index in range(self._traces.topLevelItemCount()):
            item = self._traces.topLevelItem(index)
            if item.data(0, _ROLE_ROUTE_ID) == route_id:
                trace_item = item
                break

        if trace_item is not None:
            self._traces.setCurrentItem(trace_item)
            trace_item.setExpanded(True)
            self._traces.scrollToItem(trace_item)
            self._tabs.setCurrentIndex(1)
            return True

        if route_item is not None:
            self._tabs.setCurrentIndex(0)
            return True

        return False

    def refresh(self) -> None:
        store = self._store
        requests = store.total_requests()
        errors = store.error_count()

        self._requests.set_value(str(requests))
        self._errors.set_value(str(errors))
        self._error_rate.set_value(
            f"{store.error_rate() * 100:.1f}%"
            if requests
            else "-"
        )
        self._validation.set_value(str(store.validation_failure_count()))
        self._exceptions.set_value(str(store.exception_count()))
        self._external.set_value(str(store.external_span_count()))
        self._average.set_value(_ms(store.average_latency_ms()))
        self._p50.set_value(_ms(store.latency_percentile(0.50)))
        self._p95.set_value(_ms(store.latency_percentile(0.95)))

        control = store.latest_control()
        if control is None:
            status = "Native FastAPI telemetry: waiting for debug-server capture."
        elif control.event == "capture_configured":
            status = (
                "Native FastAPI telemetry: LIVE"
                + (
                    f" | FastAPI {control.fastapi_version}"
                    if control.fastapi_version
                    else ""
                )
                + " | traces + FastAPI logs"
            )
        else:
            status = (
                "Native FastAPI telemetry: unavailable"
                + (f" | {control.message}" if control.message else "")
            )
        if store.invalid_lines:
            status += f" | {store.invalid_lines} malformed event(s) ignored"
        self._status.setText(status)

        self._timeline.set_samples(store.recent_server_spans())
        self._populate_routes()
        self._populate_traces()
        self._populate_logs()

    def _populate_routes(self) -> None:
        self._routes.clear()
        for summary in self._store.route_summaries():
            item = QTreeWidgetItem(
                [
                    summary.route_id,
                    str(summary.request_count),
                    str(summary.error_count),
                    f"{summary.error_rate * 100:.1f}%",
                    str(summary.validation_failure_count),
                    _ms(summary.average_ms),
                    _ms(summary.p50_ms),
                    _ms(summary.p95_ms),
                    _ms(summary.last_ms),
                    (
                        str(summary.last_status_code)
                        if summary.last_status_code is not None
                        else "-"
                    ),
                ]
            )
            item.setData(0, _ROLE_ROUTE_ID, summary.route_id)
            self._routes.addTopLevelItem(item)

        for column in range(self._routes.columnCount()):
            self._routes.resizeColumnToContents(column)

    @staticmethod
    def _span_function(span: NativeTelemetrySpan) -> str:
        value = span.attributes.get("code.function.name")
        return str(value) if value is not None else "-"

    def _populate_traces(self) -> None:
        selected_trace = None
        current = self._traces.currentItem()
        if current is not None:
            selected_trace = current.data(0, _ROLE_TRACE_ID)

        self._traces.clear()
        selected_item = None
        first_item = None

        for trace_id in self._store.trace_ids()[:50]:
            spans = self._store.trace_spans(trace_id)
            root = self._store.trace_root(trace_id)
            if root is None:
                continue

            by_parent: dict[str | None, list[NativeTelemetrySpan]] = defaultdict(list)
            for span in spans:
                by_parent[span.parent_span_id].append(span)

            route = root.attributes.get("http.route")
            method = root.attributes.get("http.request.method")
            route_id = (
                f"{method} {route}"
                if isinstance(method, str) and isinstance(route, str)
                else root.name
            )
            status_code = root.attributes.get("http.response.status_code")
            root_item = QTreeWidgetItem(
                [
                    route_id,
                    span_category(root),
                    root.kind,
                    _ms(root.duration_ms),
                    str(status_code or root.status or "-"),
                    self._span_function(root),
                ]
            )
            root_item.setData(0, _ROLE_TRACE_ID, trace_id)
            root_item.setData(0, _ROLE_ROUTE_ID, route_id)
            root_item.setToolTip(0, trace_id)
            self._traces.addTopLevelItem(root_item)
            if first_item is None:
                first_item = root_item
            if selected_trace == trace_id:
                selected_item = root_item

            def add_children(parent_item, parent_span_id: str) -> None:
                children = sorted(
                    by_parent.get(parent_span_id, []),
                    key=lambda span: (span.start_ns, span.end_ns),
                )
                for span in children:
                    function_name = self._span_function(span)
                    target = span_target(span)
                    function_or_target = (
                        function_name
                        if function_name != "-"
                        else (target or "-")
                    )
                    child = QTreeWidgetItem(
                        [
                            span.name,
                            span_category(span),
                            span.kind,
                            _ms(span.duration_ms),
                            span.status or "-",
                            function_or_target,
                        ]
                    )
                    child.setData(0, _ROLE_TRACE_ID, trace_id)
                    child.setData(0, _ROLE_ROUTE_ID, route_id)
                    if function_name != "-":
                        child.setData(0, _ROLE_FUNCTION, function_name)
                    parent_item.addChild(child)
                    add_children(child, span.span_id)

            add_children(root_item, root.span_id)
            root_item.setExpanded(True)

        for column in range(self._traces.columnCount()):
            self._traces.resizeColumnToContents(column)

        target = selected_item or first_item
        if target is not None:
            self._traces.setCurrentItem(target)
        else:
            self._waterfall.set_trace([])

    def _populate_logs(self) -> None:
        self._logs.clear()
        for log in reversed(self._store.logs[-200:]):
            route = log.attributes.get("http.route")
            trace = log.trace_id or "-"
            item = QTreeWidgetItem(
                [
                    log.severity or "-",
                    log.event_name or "-",
                    str(route or "-"),
                    log.body or log.exception_type or "-",
                    trace[-8:] if trace != "-" else "-",
                ]
            )
            item.setToolTip(4, trace)
            self._logs.addTopLevelItem(item)

        for column in range(self._logs.columnCount()):
            self._logs.resizeColumnToContents(column)

    def _route_activated(self, item: QTreeWidgetItem, _column: int) -> None:
        route_id = item.data(0, _ROLE_ROUTE_ID)
        if route_id:
            self.sig_route_selected.emit(str(route_id))

    def _trace_selected(
        self,
        item: QTreeWidgetItem | None,
        _previous,
    ) -> None:
        if item is None:
            self._waterfall.set_trace([])
            return
        trace_id = item.data(0, _ROLE_TRACE_ID)
        if not trace_id:
            self._waterfall.set_trace([])
            return
        self._waterfall.set_trace(
            self._store.trace_spans(str(trace_id))
        )

    def _trace_activated(self, item: QTreeWidgetItem, _column: int) -> None:
        function_name = item.data(0, _ROLE_FUNCTION)
        if function_name:
            self.sig_function_selected.emit(str(function_name))
            return

        route_id = item.data(0, _ROLE_ROUTE_ID)
        if route_id:
            self.sig_route_selected.emit(str(route_id))
