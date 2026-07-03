class ScrcpyPlayer {
    constructor(options = {}) {
        this.canvas = options.canvas;
        this.container = options.container;
        this.deviceId = options.deviceId || null;
        this.enableControl = options.enableControl || false;
        this.isVisible = options.isVisible !== false;
        this.onStatusChange = options.onStatusChange || (() => {});
        this.onError = options.onError || (() => {});
        this.onFrameDecoded = options.onFrameDecoded || (() => {});

        this.ws = null;
        this.decoder = null;
        this.status = 'disconnected';
        this._reconnectTimer = null;
        this._connectTimer = null;
        this._connectGeneration = 0;
        this._suppressReconnect = false;
        this._pollingActive = false;
        this._pollTimer = null;
        this._deviceWidth = 0;
        this._deviceHeight = 0;
        this._ctx = this.canvas ? this.canvas.getContext('2d') : null;
        this._pendingConfigData = null;
        this._isAnnexBStream = false;

        this._decoderErrored = false;
        this._decoderClosed = false;
        this._decodedFrameCount = 0;
        this._resizeObserver = null;

        this._initWebCodecsCheck();
        this._initResizeHandling();

        if (this.canvas && this.enableControl) {
            this._initControlEvents();
        }
    }

    _initResizeHandling() {
        if (!this.container) return;
        const resize = () => this._resizeCanvas();
        if (typeof ResizeObserver !== 'undefined') {
            this._resizeObserver = new ResizeObserver(resize);
            this._resizeObserver.observe(this.container);
        } else {
            window.addEventListener('resize', resize);
            this._resizeObserver = {
                disconnect: () => window.removeEventListener('resize', resize),
            };
        }
    }

    _initWebCodecsCheck() {
        this._webCodecsSupported = typeof VideoDecoder !== 'undefined' &&
            typeof EncodedVideoChunk !== 'undefined' &&
            (location.protocol === 'https:' || location.hostname === 'localhost' || location.hostname === '127.0.0.1');
    }

    connectDevice(deviceId) {
        if (!deviceId) return;
        if (
            this.deviceId === deviceId &&
            this.ws &&
            (this.ws.readyState === WebSocket.CONNECTING || this.ws.readyState === WebSocket.OPEN)
        ) {
            return;
        }

        this.deviceId = deviceId;
        this._suppressReconnect = false;
        this._decoderErrored = false;
        this._disconnectInternal(true);
        this._pendingConfigData = null;
        const generation = ++this._connectGeneration;
        this._connectTimer = setTimeout(() => {
            if (generation === this._connectGeneration) {
                this._connectScrcpy(generation);
            }
        }, 100);
    }

    _connectScrcpy(generation = this._connectGeneration) {
        if (generation !== this._connectGeneration) return;
        if (!this.deviceId || !this.isVisible) return;
        this._suppressReconnect = false;
        this._setStatus('connecting');

        if (!this._webCodecsSupported) {
            this._startPolling();
            return;
        }

        this.ws = new WebSocket(`ws://${location.host}/ws/device-screen`);
        this.ws.binaryType = 'arraybuffer';
        this.ws.onopen = () => {
            if (generation !== this._connectGeneration) return;
            console.info('Scrcpy websocket opened:', this.deviceId);
            this.ws.send(JSON.stringify({ device_id: this.deviceId }));
        };

        this.ws.onmessage = (event) => {
            if (generation !== this._connectGeneration) return;
            try {
                if (event.data instanceof Blob || event.data instanceof ArrayBuffer || ArrayBuffer.isView(event.data)) {
                    // 处理二进制帧
                    this._handleBinaryMessage(event.data);
                } else {
                    // 处理JSON消息（用于metadata等）
                    this._handleMessage(event);
                }
            } catch (e) {
                this._decoderErrored = true;
                this._handleError('message_error', e.message);
            }
        };

        this.ws.onclose = (event) => {
            if (generation !== this._connectGeneration) return;
            console.info('Scrcpy websocket closed:', {
                deviceId: this.deviceId,
                code: event.code,
                reason: event.reason,
                wasClean: event.wasClean,
            });
            if (!this._pollingActive) {
                this._setStatus('disconnected');
            }
            if (!this._suppressReconnect && this.isVisible && !this._pollingActive) {
                this._reconnectTimer = setTimeout(() => this._connectScrcpy(generation), 3000);
            }
        };

        this.ws.onerror = () => {
            if (generation !== this._connectGeneration) return;
            this._handleError('connection_failed', 'WebSocket connection failed');
        };
    }

    _handleMessage(event) {
        // 获取消息数据，处理 event 对象和直接数据两种情况
        const data = event.data || event;
        
        // 如果是 Blob 对象，应该用 _handleBinaryMessage 处理
        if (data instanceof Blob || data instanceof ArrayBuffer || ArrayBuffer.isView(data)) {
            console.warn('Binary data received in _handleMessage, redirecting to _handleBinaryMessage');
            this._handleBinaryMessage(data);
            return;
        }

        if (String(data) === '[object Blob]') {
            this._handleError('message_error', 'Binary frame was coerced to string before decoding');
            return;
        }
        
        // 如果已经是解析后的对象，直接使用
        if (typeof data === 'object' && !ArrayBuffer.isView(data)) {
            this._processMessageData(data);
            return;
        }
        
        // 尝试解析 JSON
        let msg;
        try {
            msg = JSON.parse(data);
        } catch (e) {
            console.error('Failed to parse JSON:', e.message, data);
            this._handleError('message_error', `Invalid JSON: ${e.message}`);
            return;
        }
        
        this._processMessageData(msg);
    }
    
    _processMessageData(msg) {

        if (msg.fallback) {
            this._pollingActive = true;
            this._setStatus('polling');
            return;
        }

        if (msg.is_fallback) {
            if (msg.image_base64 && this.canvas) {
                const img = new Image();
                img.onload = () => {
                    if (!this._deviceWidth || !this._deviceHeight) {
                        this._deviceWidth = msg.width || img.naturalWidth || img.width;
                        this._deviceHeight = msg.height || img.naturalHeight || img.height;
                        this._resizeCanvas();
                    }
                    this._ctx.drawImage(img, 0, 0, this.canvas.width, this.canvas.height);
                    this.onFrameDecoded(this._deviceWidth, this._deviceHeight);
                };
                img.src = 'data:image/png;base64,' + msg.image_base64;
            }
            return;
        }

        if (msg.error) {
            this._handleError(msg.error.type, msg.error.message);
            return;
        }

        if (msg.width) {
            this._deviceWidth = msg.width;
            this._deviceHeight = msg.height;
            this._resizeCanvas();
            this._setStatus('streaming');
            return;
        }

        // 兼容旧版JSON格式的数据帧
        if (msg.data) {
            const binary = Uint8Array.from(atob(msg.data), c => c.charCodeAt(0));

            if (msg.isConfig) {
                this._pendingConfigData = binary;
                this._initDecoder();
                return;
            }

            if (this.decoder && !this._decoderClosed && !this._decoderErrored) {
                const pts = (msg.pts || 0) & 0x3FFFFFFFFFFFFFFF;
                const chunkData = this._prepareChunkData(binary, msg.isKeyframe);
                try {
                    const chunk = new EncodedVideoChunk({
                        type: msg.isKeyframe ? 'key' : 'delta',
                        timestamp: pts,
                        data: chunkData,
                    });
                    this.decoder.decode(chunk);
                } catch (e) {
                    this._decoderErrored = true;
                    this._handleError('decoder_error', e.message);
                }
            }
        }
    }

    async _handleBinaryMessage(binaryMessage) {
        const arrayBuffer = binaryMessage instanceof ArrayBuffer
            ? binaryMessage
            : ArrayBuffer.isView(binaryMessage)
                ? binaryMessage.buffer.slice(binaryMessage.byteOffset, binaryMessage.byteOffset + binaryMessage.byteLength)
                : await binaryMessage.arrayBuffer();
        const data = new Uint8Array(arrayBuffer);
        
        // 帧格式：1字节类型 + 8字节PTS + 4字节长度 + 数据
        if (data.length < 13) {
            console.warn('Invalid binary frame: too short');
            return;
        }

        const frameType = data[0];  // 0=配置帧, 1=关键帧, 2=普通帧
        let pts = 0;
        for (let i = 0; i < 8; i++) {
            pts = (pts << 8) | data[1 + i];
        }
        let dataLen = 0;
        for (let i = 0; i < 4; i++) {
            dataLen = (dataLen << 8) | data[9 + i];
        }

        const payload = data.slice(13, 13 + dataLen);
        const isConfig = frameType === 0;
        const isKeyframe = frameType === 1;

        if (isConfig) {
            this._pendingConfigData = payload;
            this._initDecoder();
            return;
        }

        if (this.decoder && !this._decoderClosed && !this._decoderErrored) {
            const chunkData = this._prepareChunkData(payload, isKeyframe);
            try {
                const chunk = new EncodedVideoChunk({
                    type: isKeyframe ? 'key' : 'delta',
                    timestamp: pts,
                    data: chunkData,
                });
                this.decoder.decode(chunk);
            } catch (e) {
                this._decoderErrored = true;
                this._handleError('decoder_error', e.message);
            }
        }
    }

    _renderFrame() {
        if (this._pendingFrame && this._ctx && this.canvas) {
            try {
                this._ctx.drawImage(this._pendingFrame, 0, 0, this.canvas.width, this.canvas.height);
            } catch (e) {
                console.warn('Failed to render frame:', e);
            }
            try { this._pendingFrame.close(); } catch (e) {}
            this._pendingFrame = null;
        }
        this._isRendering = false;
    }

    _initDecoder() {
        this._decoderClosed = false;
        this._decoderErrored = false;
        this._decodedFrameCount = 0;
        this._pendingFrame = null;
        this._isRendering = false;
        if (this.decoder) {
            try { this.decoder.close(); } catch (e) {}
            this.decoder = null;
        }
        this.decoder = new VideoDecoder({
            output: (frame) => {
                // 使用requestAnimationFrame优化渲染
                if (this._ctx && this.canvas) {
                    // 丢弃旧帧，只保留最新帧
                    if (this._pendingFrame) {
                        try { this._pendingFrame.close(); } catch (e) {}
                    }
                    this._pendingFrame = frame;
                    
                    this._decodedFrameCount += 1;
                    if (this._decodedFrameCount === 1) {
                        console.info('Scrcpy first decoded frame:', frame.displayWidth, frame.displayHeight);
                        this.onFrameDecoded(frame.displayWidth, frame.displayHeight);
                    }
                    
                    if (!this._isRendering) {
                        this._isRendering = true;
                        requestAnimationFrame(() => this._renderFrame());
                    }
                } else {
                    frame.close();
                }
            },
            error: (e) => {
                this._decoderErrored = true;
                this._handleError('decoder_error', e.message);
            },
        });

        try {
            const config = this._createDecoderConfig();
            console.info('Scrcpy decoder config:', config);
            this.decoder.configure(config);
        } catch (e) {
            this._decoderErrored = true;
            this._handleError('decoder_error', e.message);
        }
    }

    _createDecoderConfig() {
        const configData = this._pendingConfigData;
        const isAnnexB = this._isAnnexB(configData);
        this._isAnnexBStream = isAnnexB;
        const config = {
            codec: this._resolveCodecString(),
            codedWidth: this._deviceWidth,
            codedHeight: this._deviceHeight,
        };

        if (isAnnexB) {
            config.avc = { format: 'annexb' };
        } else if (configData) {
            config.description = configData;
            config.avc = { format: 'avc' };
        }

        return config;
    }

    _prepareChunkData(data, isKeyframe) {
        if (!this._isAnnexBStream || !isKeyframe || !this._pendingConfigData) {
            return data;
        }

        if (this._startsWithConfig(data)) {
            return data;
        }

        const combined = new Uint8Array(this._pendingConfigData.length + data.length);
        combined.set(this._pendingConfigData, 0);
        combined.set(data, this._pendingConfigData.length);
        return combined;
    }

    _startsWithConfig(data) {
        const config = this._pendingConfigData;
        if (!config || data.length < config.length) {
            return false;
        }

        for (let i = 0; i < config.length; i++) {
            if (data[i] !== config[i]) {
                return false;
            }
        }
        return true;
    }

    _resolveCodecString() {
        const defaultCodec = 'avc1.42001E';
        const config = this._pendingConfigData;
        if (!config || config.length < 4) {
            return defaultCodec;
        }

        if (this._isAnnexB(config)) {
            const sps = this._findAnnexBNalUnits(config).find((nal) => (nal[0] & 0x1F) === 7);
            if (!sps || sps.length < 4) {
                return defaultCodec;
            }

            const profile = sps[1].toString(16).padStart(2, '0').toUpperCase();
            const compatibility = sps[2].toString(16).padStart(2, '0').toUpperCase();
            const level = sps[3].toString(16).padStart(2, '0').toUpperCase();
            return `avc1.${profile}${compatibility}${level}`;
        }

        if (config[0] !== 0x01) {
            return defaultCodec;
        }
        const profile = config[1].toString(16).padStart(2, '0').toUpperCase();
        const compatibility = config[2].toString(16).padStart(2, '0').toUpperCase();
        const level = config[3].toString(16).padStart(2, '0').toUpperCase();
        return `avc1.${profile}${compatibility}${level}`;
    }

    _isAnnexB(data) {
        return !!data && data.length >= 4 && (
            (data[0] === 0x00 && data[1] === 0x00 && data[2] === 0x01) ||
            (data[0] === 0x00 && data[1] === 0x00 && data[2] === 0x00 && data[3] === 0x01)
        );
    }

    _findAnnexBNalUnits(data) {
        const units = [];
        let index = 0;

        const findStartCode = (from) => {
            for (let i = from; i < data.length - 2; i++) {
                if (data[i] === 0x00 && data[i + 1] === 0x00 && data[i + 2] === 0x01) {
                    return { index: i, length: 3 };
                }
                if (
                    i < data.length - 3 &&
                    data[i] === 0x00 &&
                    data[i + 1] === 0x00 &&
                    data[i + 2] === 0x00 &&
                    data[i + 3] === 0x01
                ) {
                    return { index: i, length: 4 };
                }
            }
            return null;
        };

        while (index < data.length) {
            const start = findStartCode(index);
            if (!start) break;

            const payloadStart = start.index + start.length;
            const next = findStartCode(payloadStart);
            const payloadEnd = next ? next.index : data.length;
            if (payloadEnd > payloadStart) {
                units.push(data.slice(payloadStart, payloadEnd));
            }
            index = payloadEnd;
        }

        return units;
    }

    _startPolling() {
        this._setStatus('polling');
        this._pollScreen();
    }

    async _pollScreen() {
        if (!this.isVisible || !this._pollingActive) return;
        try {
            const url = this.deviceId ? `/api/devices/screen?device_id=${encodeURIComponent(this.deviceId)}` : `/api/devices/screen`;
            const resp = await fetch(url);
            const data = await resp.json();
            if (data.image_base64 && this.canvas) {
                const img = new Image();
                img.onload = () => {
                    if (!this._deviceWidth || !this._deviceHeight) {
                        this._deviceWidth = img.naturalWidth || img.width;
                        this._deviceHeight = img.naturalHeight || img.height;
                        this._resizeCanvas();
                    }
                    this._ctx.drawImage(img, 0, 0, this.canvas.width, this.canvas.height);
                    this.onFrameDecoded(this._deviceWidth, this._deviceHeight);
                };
                img.src = 'data:image/png;base64,' + data.image_base64;
            }
        } catch (e) {
            // silent retry
        }
        // 缩短轮询间隔以降低延迟
        this._pollTimer = setTimeout(() => this._pollScreen(), 200);
    }

    _resizeCanvas() {
        if (!this.canvas || !this.container) return;
        if (!this._deviceWidth || !this._deviceHeight) return;

        const screen = this.canvas.parentElement || this.container;
        const frame = screen.closest ? screen.closest('.phone-container') : null;
        if (frame) {
            const stageRect = this.container.getBoundingClientRect();
            const aspect = this._deviceWidth / this._deviceHeight;
            let frameWidth = stageRect.width;
            let frameHeight = frameWidth / aspect;
            if (frameHeight > stageRect.height) {
                frameHeight = stageRect.height;
                frameWidth = frameHeight * aspect;
            }
            frame.style.width = `${Math.floor(frameWidth)}px`;
            frame.style.height = `${Math.floor(frameHeight)}px`;
        }

        const rect = screen.getBoundingClientRect();
        if (!rect.width || !rect.height) return;

        const dpr = Math.min(window.devicePixelRatio || 1, 2);
        const canvasWidth = Math.max(1, Math.floor(rect.width * dpr));
        const canvasHeight = Math.max(1, Math.floor(rect.height * dpr));
        if (this.canvas.width !== canvasWidth || this.canvas.height !== canvasHeight) {
            this.canvas.width = canvasWidth;
            this.canvas.height = canvasHeight;
        }
        this.canvas.style.width = '100%';
        this.canvas.style.height = '100%';
    }

    _setStatus(status) {
        this.status = status;
        this.onStatusChange(status);
    }

    _handleError(type, message) {
        console.warn('Scrcpy player error:', type, message);
        this._disconnectInternal(true);
        if (
            type === 'device_offline' ||
            type === 'connection_failed' ||
            type === 'message_error' ||
            type === 'decoder_error' ||
            type === 'unknown' ||
            type === 'start_failed'
        ) {
            this._pollingActive = true;
            this._startPolling();
        }
        this.onError({ type, message });
    }

    _initControlEvents() {
        this.canvas.addEventListener('click', (e) => {
            if (this.status !== 'streaming' && this.status !== 'polling') return;
            const rect = this.canvas.getBoundingClientRect();
            const scaleX = this._deviceWidth / rect.width;
            const scaleY = this._deviceHeight / rect.height;
            const x = Math.round((e.clientX - rect.left) * scaleX);
            const y = Math.round((e.clientY - rect.top) * scaleY);
            fetch('/api/control/tap', {
                method: 'POST',
                headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                body: `x=${x}&y=${y}&device_id=${this.deviceId || ''}`,
            });
        });
    }

    disconnectDevice(suppressReconnect = true) {
        this._suppressReconnect = suppressReconnect;
        this._pollingActive = false;
        this._disconnectInternal(suppressReconnect);
        this._setStatus('disconnected');
    }

    _disconnectInternal(suppressReconnect) {
        if (this._reconnectTimer) {
            clearTimeout(this._reconnectTimer);
            this._reconnectTimer = null;
        }
        if (this._connectTimer) {
            clearTimeout(this._connectTimer);
            this._connectTimer = null;
        }
        if (this._pollTimer) {
            clearTimeout(this._pollTimer);
            this._pollTimer = null;
        }
        if (this.decoder) {
            this._decoderClosed = true;
            try { this.decoder.close(); } catch (e) {}
            this.decoder = null;
        }
        if (this.ws) {
            this.ws.close();
            this.ws = null;
        }
        this._pendingConfigData = null;
        this._suppressReconnect = suppressReconnect;
    }

    setVisible(visible) {
        this.isVisible = visible;
        if (visible && this.deviceId && this.status === 'disconnected') {
            setTimeout(() => this._connectScrcpy(), 100);
        } else if (!visible) {
            this._disconnectInternal(false);
        }
    }

    destroy() {
        this._disconnectInternal(true);
        if (this._resizeObserver) {
            this._resizeObserver.disconnect();
            this._resizeObserver = null;
        }
        this.canvas = null;
        this.container = null;
        this._ctx = null;
    }
}
