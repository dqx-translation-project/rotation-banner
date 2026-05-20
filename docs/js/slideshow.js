(function () {
    var INTERVAL = 5000;
    var current = 0;
    var timer;

    var slides = document.querySelectorAll('.slide');
    var dots = document.querySelectorAll('.dot');

    if (slides.length === 0) return;

    function showSlide(n) {
        slides[current].classList.remove('active');
        dots[current].classList.remove('active');
        current = ((n % slides.length) + slides.length) % slides.length;
        slides[current].classList.add('active');
        dots[current].classList.add('active');
    }

    function startTimer() {
        timer = setInterval(function () { showSlide(current + 1); }, INTERVAL);
    }

    function resetTimer() {
        clearInterval(timer);
        startTimer();
    }

    dots.forEach(function (dot) {
        dot.addEventListener('click', function () {
            showSlide(parseInt(dot.getAttribute('data-index'), 10));
            resetTimer();
        });
    });

    startTimer();
}());
