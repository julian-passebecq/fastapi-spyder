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
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from spyder_fastapi.core.telemetry import NativeTelemetryStore
from spyder_fastapi.models import NativeTelemetrySpan


_ROLE_ROUTE_ID = 40
_ROLE_TRACE_ID = 41


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
        self._average = _KpiBox("Average")
        self._p50 = _KpiBox("P50")
        self._p95 = _KpiBox("P95")
        for widget in (
            self._requests,
            self._errors,
            self._error_rate,
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
            ["Trace / span", "Kind", "Duration", "Status", "Function"]
        )
        self._traces.setRootIsDecorated(True)
        self._traces.itemDoubleClicked.connect(self._trace_activated)

        self._logs = QTreeWidget()
        self._logs.setRootIsDecorated(False)
        self._logs.setHeaderLabels(
            ["Severity", "Event", "Route", "Message", "Trace"]
        )

        tabs = QTabWidget()
        tabs.addTab(self._routes, "Routes")
        tabs.addTab(self._traces, "Traces / waterfall")
        tabs.addTab(self._logs, "FastAPI logs")

        layout = QVBoxLayout(self)
        layout.addLayout(header)
        layout.addLayout(kpis)
        layout.addWidget(self._timeline)
        layout.addWidget(tabs, 1)

        self.refresh()

    def set_store(self, store: NativeTelemetryStore) -> None:
        self._store = store
        self.refresh()

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
        self._traces.clear()

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

            def add_children(parent_item, parent_span_id: str) -> None:
                children = sorted(
                    by_parent.get(parent_span_id, []),
                    key=lambda span: (span.start_ns, span.end_ns),
                )
                for span in children:
                    child = QTreeWidgetItem(
                        [
                            span.name,
                            span.kind,
                            _ms(span.duration_ms),
                            span.status or "-",
                            self._span_function(span),
                        ]
                    )
                    child.setData(0, _ROLE_TRACE_ID, trace_id)
                    child.setData(0, _ROLE_ROUTE_ID, route_id)
                    parent_item.addChild(child)
                    add_children(child, span.span_id)

            add_children(root_item, root.span_id)
            root_item.setExpanded(True)

        for column in range(self._traces.columnCount()):
            self._traces.resizeColumnToContents(column)

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

    def _trace_activated(self, item: QTreeWidgetItem, _column: int) -> None:
        route_id = item.data(0, _ROLE_ROUTE_ID)
        if route_id:
            self.sig_route_selected.emit(str(route_id))
