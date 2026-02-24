(function () {
  const BRIDGE_VERSION = '20260224g';
  if (window.BacktesterLWC && window.BacktesterLWC.__version === BRIDGE_VERSION) {
    return;
  }

  const registry = new Map();

  const chartDefaults = {
    layout: {
      background: { color: '#0f172a' },
      textColor: '#e2e8f0',
      attributionLogo: true,
    },
    grid: {
      vertLines: { color: 'rgba(148, 163, 184, 0.16)' },
      horzLines: { color: 'rgba(148, 163, 184, 0.16)' },
    },
    crosshair: {
      mode: 1,
      vertLine: { color: 'rgba(148, 163, 184, 0.35)', width: 1, style: 2 },
      horzLine: { color: 'rgba(148, 163, 184, 0.35)', width: 1, style: 2 },
    },
    rightPriceScale: {
      borderColor: 'rgba(148, 163, 184, 0.3)',
      scaleMargins: { top: 0.1, bottom: 0.1 },
    },
    timeScale: {
      borderColor: 'rgba(148, 163, 184, 0.3)',
      timeVisible: true,
      secondsVisible: false,
      rightOffset: 6,
      barSpacing: 8,
      minBarSpacing: 4,
    },
    handleScroll: {
      mouseWheel: true,
      pressedMouseMove: true,
      horzTouchDrag: true,
      vertTouchDrag: true,
    },
    handleScale: {
      axisPressedMouseMove: true,
      mouseWheel: true,
      pinch: true,
    },
    autoSize: true,
  };

  const seriesDefaults = {
    upColor: '#22c55e',
    downColor: '#ef4444',
    borderUpColor: '#22c55e',
    borderDownColor: '#ef4444',
    wickUpColor: '#22c55e',
    wickDownColor: '#ef4444',
  };

  function toNumber(value, fallback) {
    const num = Number(value);
    return Number.isFinite(num) ? num : fallback;
  }

  function resolveContainer(containerId) {
    if (typeof getHtmlElement === 'function') {
      const element = getHtmlElement(containerId);
      if (element) {
        return element;
      }
    }
    return document.getElementById(String(containerId));
  }

  function normalizeBars(rawBars) {
    if (!Array.isArray(rawBars)) {
      return [];
    }
    return rawBars
      .map((bar) => {
        if (!bar) {
          return null;
        }
        const time = toNumber(bar.time, null);
        const open = toNumber(bar.open, null);
        const high = toNumber(bar.high, null);
        const low = toNumber(bar.low, null);
        const close = toNumber(bar.close, null);
        if (time === null || open === null || high === null || low === null || close === null) {
          return null;
        }
        return { time, open, high, low, close };
      })
      .filter((bar) => bar !== null)
      .sort((a, b) => a.time - b.time);
  }

  function normalizeMarkers(rawMarkers) {
    if (!Array.isArray(rawMarkers)) {
      return [];
    }
    return rawMarkers
      .map((marker) => {
        if (!marker) {
          return null;
        }
        const time = toNumber(marker.time, null);
        const price = toNumber(marker.price, null);
        const logical = toNumber(marker.logical, null);
        if (time === null || price === null) {
          return null;
        }
        return {
          time,
          price,
          logical,
          text: String(marker.text || ''),
          color: String(marker.color || '#22c55e'),
          shape: marker.shape === 'arrowDown' ? 'arrowDown' : 'arrowUp',
        };
      })
      .filter((marker) => marker !== null)
      .sort((a, b) => a.time - b.time);
  }

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function nearestBar(state, marker) {
    if (!state || !Array.isArray(state.bars) || !state.bars.length || !marker) {
      return null;
    }

    if (Number.isFinite(marker.logical)) {
      const idx = clamp(Math.round(marker.logical), 0, state.bars.length - 1);
      return state.bars[idx] || null;
    }

    const target = toNumber(marker.time, null);
    if (target === null) {
      return null;
    }

    let lo = 0;
    let hi = state.bars.length - 1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      const midTime = toNumber(state.bars[mid] && state.bars[mid].time, null);
      if (midTime === null) {
        return null;
      }
      if (midTime === target) {
        return state.bars[mid];
      }
      if (midTime < target) {
        lo = mid + 1;
      } else {
        hi = mid - 1;
      }
    }

    const leftIdx = clamp(hi, 0, state.bars.length - 1);
    const rightIdx = clamp(lo, 0, state.bars.length - 1);
    const leftBar = state.bars[leftIdx];
    const rightBar = state.bars[rightIdx];
    const leftTime = toNumber(leftBar && leftBar.time, null);
    const rightTime = toNumber(rightBar && rightBar.time, null);
    if (leftTime === null) {
      return rightBar || null;
    }
    if (rightTime === null) {
      return leftBar || null;
    }
    return Math.abs(target - leftTime) <= Math.abs(rightTime - target) ? leftBar : rightBar;
  }

  function formatPrice(value) {
    const price = toNumber(value, null);
    if (price === null) {
      return '';
    }
    const abs = Math.abs(price);
    if (abs >= 1000) {
      return price.toFixed(2);
    }
    if (abs >= 1) {
      return price.toFixed(2);
    }
    if (abs >= 0.01) {
      return price.toFixed(4);
    }
    return price.toExponential(2);
  }

  function applyNativeArrowMarkers(state) {
    if (!state || !state.series || !Array.isArray(state.markers)) {
      return;
    }

    const mapped = state.markers
      .map((marker) => {
        const bar = nearestBar(state, marker);
        if (!bar) {
          return null;
        }
        const time = toNumber(bar.time, null);
        if (time === null) {
          return null;
        }
        const shape = marker.shape === 'arrowDown' ? 'arrowDown' : 'arrowUp';
        return {
          time,
          position: shape === 'arrowDown' ? 'aboveBar' : 'belowBar',
          color: String(marker.color || '#22c55e'),
          shape,
          text: '',
        };
      })
      .filter((item) => item !== null);

    if (window.LightweightCharts && typeof window.LightweightCharts.createSeriesMarkers === 'function') {
      if (state.nativeMarkers && typeof state.nativeMarkers.setMarkers === 'function') {
        state.nativeMarkers.setMarkers(mapped);
      } else {
        state.nativeMarkers = window.LightweightCharts.createSeriesMarkers(state.series, mapped);
      }
      return;
    }

    if (typeof state.series.setMarkers === 'function') {
      state.series.setMarkers(mapped);
    }
  }

  function createHud(container) {
    container.style.position = 'relative';

    const hud = document.createElement('div');
    hud.style.position = 'absolute';
    hud.style.inset = '0';
    hud.style.pointerEvents = 'none';
    hud.style.zIndex = '2';

    const title = document.createElement('div');
    title.style.position = 'absolute';
    title.style.top = '8px';
    title.style.left = '12px';
    title.style.fontSize = '12px';
    title.style.letterSpacing = '0.02em';
    title.style.color = '#cbd5e1';

    const message = document.createElement('div');
    message.style.position = 'absolute';
    message.style.top = '50%';
    message.style.left = '50%';
    message.style.transform = 'translate(-50%, -50%)';
    message.style.fontSize = '13px';
    message.style.color = '#94a3b8';
    message.style.textAlign = 'center';
    message.style.maxWidth = '80%';

    const markerLayer = document.createElement('div');
    markerLayer.style.position = 'absolute';
    markerLayer.style.inset = '0';
    markerLayer.style.zIndex = '3';

    hud.appendChild(title);
    hud.appendChild(message);
    hud.appendChild(markerLayer);
    container.appendChild(hud);

    return { title, message, markerLayer };
  }

  function drawMarkers(state) {
    if (!state || !state.markerLayer) {
      return;
    }

    state.markerLayer.replaceChildren();

    for (const marker of state.markers) {
      let y = null;
      if (state.series && typeof state.series.priceToCoordinate === 'function') {
        y = state.series.priceToCoordinate(marker.price);
      }
      if (y === null || y === undefined) {
        const nearest = nearestBar(state, marker);
        if (nearest && state.series && typeof state.series.priceToCoordinate === 'function') {
          y = state.series.priceToCoordinate(nearest.close);
        }
      }
      if (!Number.isFinite(y)) {
        y = null;
      }
      if (y === null || y === undefined) {
        continue;
      }
      const yRounded = Math.round(y);
      if (yRounded < -2 || yRounded > (state.container.clientHeight || 0) + 2) {
        continue;
      }

      const row = document.createElement('div');
      row.style.position = 'absolute';
      row.style.left = '0';
      row.style.right = '0';
      row.style.top = `${yRounded}px`;
      row.style.transform = 'translateY(-50%)';
      row.style.display = 'flex';
      row.style.alignItems = 'center';
      row.style.gap = '8px';
      row.style.pointerEvents = 'none';

      const line = document.createElement('div');
      line.style.flex = '1';
      line.style.borderTop = `2px solid ${marker.color}`;
      line.style.opacity = '0.85';
      line.style.boxShadow = '0 0 4px rgba(15, 23, 42, 0.8)';

      const label = document.createElement('div');
      const text = String(marker.text || '').trim();
      const priceText = formatPrice(marker.price);
      label.textContent = `${text}${text && priceText ? ' ' : ''}${priceText}`;
      label.style.fontSize = '11px';
      label.style.fontWeight = '700';
      label.style.lineHeight = '14px';
      label.style.padding = '1px 6px';
      label.style.marginRight = '8px';
      label.style.borderRadius = '10px';
      label.style.color = marker.color;
      label.style.background = 'rgba(15, 23, 42, 0.82)';
      label.style.border = `1px solid ${marker.color}`;
      label.style.textShadow = '0 0 4px rgba(15, 23, 42, 0.8)';
      label.style.whiteSpace = 'nowrap';

      row.appendChild(line);
      row.appendChild(label);
      state.markerLayer.appendChild(row);
    }
  }

  function ensure(containerId, options = {}) {
    const key = String(containerId);
    let state = registry.get(key);

    const container = resolveContainer(containerId);
    if (!container) {
      return null;
    }

    if (state) {
      return state;
    }

    if (!window.LightweightCharts || typeof window.LightweightCharts.createChart !== 'function') {
      return null;
    }

    if (options.height) {
      container.style.height = `${Math.max(200, toNumber(options.height, 400))}px`;
    }

    const chart = window.LightweightCharts.createChart(container, {
      ...chartDefaults,
      ...(options.chartOptions || {}),
    });

    let series = null;
    try {
      if (
        typeof chart.addSeries === 'function' &&
        window.LightweightCharts &&
        window.LightweightCharts.CandlestickSeries
      ) {
        series = chart.addSeries(window.LightweightCharts.CandlestickSeries, seriesDefaults);
      } else if (typeof chart.addCandlestickSeries === 'function') {
        series = chart.addCandlestickSeries(seriesDefaults);
      }
    } catch (_error) {
      series = null;
    }
    if (!series) {
      chart.remove();
      return null;
    }

    const hud = createHud(container);

    const resizeObserver = new ResizeObserver(() => {
      const width = Math.max(200, Math.floor(container.clientWidth || 200));
      const height = Math.max(200, Math.floor(container.clientHeight || 200));
      chart.resize(width, height);
      drawMarkers(state);
    });
    resizeObserver.observe(container);

    state = {
      key,
      container,
      chart,
      series,
      resizeObserver,
      markers: [],
      bars: [],
      titleElement: hud.title,
      messageElement: hud.message,
      markerLayer: hud.markerLayer,
      nativeMarkers: null,
      redrawTimer: null,
    };

    state.redrawTimer = window.setInterval(() => {
      drawMarkers(state);
    }, 180);

    chart.timeScale().subscribeVisibleLogicalRangeChange(() => {
      drawMarkers(state);
    });

    registry.set(key, state);
    return state;
  }

  function render(payload) {
    if (!payload || payload.containerId === undefined || payload.containerId === null) {
      return;
    }

    const state = ensure(payload.containerId, { height: payload.height });
    if (!state) {
      return;
    }

    const bars = normalizeBars(payload.bars);
    const markers = normalizeMarkers(payload.markers);

    state.titleElement.textContent = String(payload.title || '');

    if (!bars.length) {
      state.series.setData([]);
      state.markers = [];
      state.bars = [];
      applyNativeArrowMarkers(state);
      state.messageElement.textContent = String(payload.emptyMessage || 'No data');
      drawMarkers(state);
      return;
    }

    state.series.setData(bars);
    state.bars = bars;
    state.markers = markers;
    applyNativeArrowMarkers(state);
    state.messageElement.textContent = '';

    drawMarkers(state);
    state.chart.timeScale().fitContent();
  }

  function clear(containerId, message) {
    if (containerId === undefined || containerId === null) {
      return;
    }
    const state = ensure(containerId, {});
    if (!state) {
      return;
    }

    state.series.setData([]);
    state.markers = [];
    state.bars = [];
    applyNativeArrowMarkers(state);
    state.messageElement.textContent = String(message || 'No data');
    drawMarkers(state);
  }

  function destroy(containerId) {
    const key = String(containerId);
    const state = registry.get(key);
    if (!state) {
      return;
    }

    if (state.redrawTimer) {
      window.clearInterval(state.redrawTimer);
    }
    if (state.resizeObserver) {
      state.resizeObserver.disconnect();
    }
    if (state.chart) {
      state.chart.remove();
    }
    registry.delete(key);
  }

  window.BacktesterLWC = {
    __version: BRIDGE_VERSION,
    ensure,
    render,
    clear,
    destroy,
  };
})();
