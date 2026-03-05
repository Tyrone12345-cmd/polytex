// Signature Pad - Unterschrift Canvas
(function() {
    const canvas = document.getElementById('signature-canvas');
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    let drawing = false;
    let hasSignature = false;

    const overlay = document.getElementById('sig-modal-overlay');
    const preview = document.getElementById('sig-preview');
    const previewCanvas = document.getElementById('sig-preview-canvas');
    const previewCtx = previewCanvas ? previewCanvas.getContext('2d') : null;
    const doneBtn = document.getElementById('sig-done-btn');

    function resizeCanvas() {
        const wrapper = canvas.parentElement;
        const rect = wrapper.getBoundingClientRect();
        canvas.width = rect.width - 8;
        canvas.height = 300;
        ctx.strokeStyle = '#222';
        ctx.lineWidth = 2.5;
        ctx.lineCap = 'round';
        ctx.lineJoin = 'round';
    }

    function resizePreview() {
        if (!previewCanvas) return;
        const rect = previewCanvas.parentElement.getBoundingClientRect();
        previewCanvas.width = rect.width - 2;
        previewCanvas.height = 100;
    }

    function copyToPreview() {
        if (!previewCanvas) return;
        resizePreview();
        if (hasSignature) {
            previewCtx.clearRect(0, 0, previewCanvas.width, previewCanvas.height);
            previewCtx.drawImage(canvas, 0, 0, previewCanvas.width, previewCanvas.height);
            preview.classList.add('has-signature');
        } else {
            previewCtx.clearRect(0, 0, previewCanvas.width, previewCanvas.height);
            preview.classList.remove('has-signature');
        }
    }

    function openModal() {
        overlay.classList.add('open');
        // Resize after transition so dimensions are correct
        setTimeout(function() {
            var data = hasSignature ? canvas.toDataURL() : null;
            resizeCanvas();
            if (data && hasSignature) {
                var img = new Image();
                img.onload = function() { ctx.drawImage(img, 0, 0, canvas.width, canvas.height); };
                img.src = data;
            }
        }, 50);
    }

    function closeModal() {
        overlay.classList.remove('open');
        copyToPreview();
    }

    // Open modal on preview click
    if (preview) {
        preview.addEventListener('click', openModal);
    }

    // Close modal on done button
    if (doneBtn) {
        doneBtn.addEventListener('click', closeModal);
    }

    // Close on overlay background click
    if (overlay) {
        overlay.addEventListener('click', function(e) {
            if (e.target === overlay) closeModal();
        });
    }

    // Init preview
    resizePreview();

    function getPos(e) {
        const rect = canvas.getBoundingClientRect();
        if (e.touches && e.touches.length > 0) {
            return {
                x: e.touches[0].clientX - rect.left,
                y: e.touches[0].clientY - rect.top
            };
        }
        return {
            x: e.clientX - rect.left,
            y: e.clientY - rect.top
        };
    }

    function startDraw(e) {
        e.preventDefault();
        drawing = true;
        const pos = getPos(e);
        ctx.beginPath();
        ctx.moveTo(pos.x, pos.y);
    }

    function draw(e) {
        if (!drawing) return;
        e.preventDefault();
        const pos = getPos(e);
        ctx.lineTo(pos.x, pos.y);
        ctx.stroke();
        hasSignature = true;
    }

    function stopDraw(e) {
        if (e) e.preventDefault();
        drawing = false;
    }

    // Mouse Events
    canvas.addEventListener('mousedown', startDraw);
    canvas.addEventListener('mousemove', draw);
    canvas.addEventListener('mouseup', stopDraw);
    canvas.addEventListener('mouseleave', stopDraw);

    // Touch Events
    canvas.addEventListener('touchstart', startDraw);
    canvas.addEventListener('touchmove', draw);
    canvas.addEventListener('touchend', stopDraw);

    // Löschen
    const clearBtn = document.getElementById('clear-btn');
    if (clearBtn) {
        clearBtn.addEventListener('click', function() {
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            hasSignature = false;
            copyToPreview();
        });
    }

    // Globale Funktion zum Abrufen der Unterschrift-Daten
    window.getSignatureData = function() {
        if (!hasSignature) return null;
        return canvas.toDataURL('image/png');
    };
})();
