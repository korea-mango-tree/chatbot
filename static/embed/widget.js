(function() {
    // 설정 읽기
    var script = document.currentScript;
    var tenant = script.getAttribute('data-tenant') || 'default';
    var color = script.getAttribute('data-color') || '#4a6cf7';
    var position = script.getAttribute('data-position') || 'bottom-right';
    var baseUrl = script.src.replace(/\/embed\/widget\.js.*$/, '');

    // 이미 로드됐으면 중복 방지
    if (document.getElementById('chatbot-widget-container')) return;

    // 스타일 주입
    var style = document.createElement('style');
    style.textContent = '\n' +
        '#chatbot-widget-container{position:fixed;z-index:999999;font-family:-apple-system,sans-serif;}\n' +
        '#chatbot-widget-container.bottom-right{bottom:20px;right:20px;}\n' +
        '#chatbot-widget-container.bottom-left{bottom:20px;left:20px;}\n' +
        '#chatbot-bubble{width:60px;height:60px;border-radius:50%;background:' + color + ';color:#fff;border:none;cursor:pointer;display:flex;align-items:center;justify-content:center;font-size:28px;box-shadow:0 4px 16px rgba(0,0,0,0.2);transition:transform 0.2s,box-shadow 0.2s;}\n' +
        '#chatbot-bubble:hover{transform:scale(1.08);box-shadow:0 6px 24px rgba(0,0,0,0.3);}\n' +
        '#chatbot-bubble.open{transform:rotate(90deg);}\n' +
        '#chatbot-iframe-wrap{display:none;position:absolute;bottom:72px;width:380px;height:580px;border-radius:16px;overflow:hidden;box-shadow:0 8px 40px rgba(0,0,0,0.2);}\n' +
        '#chatbot-widget-container.bottom-right #chatbot-iframe-wrap{right:0;}\n' +
        '#chatbot-widget-container.bottom-left #chatbot-iframe-wrap{left:0;}\n' +
        '#chatbot-iframe-wrap.open{display:block;animation:chatbot-slide-up 0.25s ease-out;}\n' +
        '#chatbot-iframe{width:100%;height:100%;border:none;border-radius:16px;}\n' +
        '@keyframes chatbot-slide-up{from{opacity:0;transform:translateY(16px);}to{opacity:1;transform:translateY(0);}}\n' +
        '@media(max-width:480px){#chatbot-iframe-wrap{width:calc(100vw - 24px);height:calc(100vh - 120px);bottom:72px;}#chatbot-widget-container.bottom-right #chatbot-iframe-wrap{right:-8px;}}\n';
    document.head.appendChild(style);

    // 컨테이너
    var container = document.createElement('div');
    container.id = 'chatbot-widget-container';
    container.className = position;

    // iframe 래퍼
    var iframeWrap = document.createElement('div');
    iframeWrap.id = 'chatbot-iframe-wrap';

    // iframe (lazy load)
    var iframe = null;

    // 채팅 버블 버튼
    var bubble = document.createElement('button');
    bubble.id = 'chatbot-bubble';
    bubble.innerHTML = '💬';
    bubble.title = '채팅 상담';

    var isOpen = false;
    bubble.addEventListener('click', function() {
        isOpen = !isOpen;
        if (isOpen) {
            // 처음 열 때 iframe 생성
            if (!iframe) {
                iframe = document.createElement('iframe');
                iframe.id = 'chatbot-iframe';
                iframe.src = baseUrl + '/embed/chat?tenant=' + encodeURIComponent(tenant);
                iframe.allow = 'microphone';
                iframeWrap.appendChild(iframe);
            }
            iframeWrap.className = 'open';
            bubble.innerHTML = '✕';
            bubble.classList.add('open');
        } else {
            iframeWrap.className = '';
            bubble.innerHTML = '💬';
            bubble.classList.remove('open');
        }
    });

    container.appendChild(iframeWrap);
    container.appendChild(bubble);
    document.body.appendChild(container);
})();
