/* Five verified AI stories. Add questions here to extend the waiting-room quiz. */
(() => {
  const QUESTIONS = [
    {
      question: 'Какую роль играл один из первых знаменитых чат-ботов — ELIZA?',
      options: ['Психотерапевта', 'Шахматного тренера', 'Диспетчера такси'],
      answer: 0,
      explanation: 'ELIZA появилась в MIT в 1960-х. Один из её сценариев поддерживал беседу в стиле психотерапевта, хотя программа не понимала собеседника так, как человек.',
      source: 'https://tcm.computerhistory.org/ExhibitKits1994.pdf',
    },
    {
      question: 'В какой игре компьютер Deep Blue победил Гарри Каспарова?',
      options: ['В го', 'В шахматах', 'В покере'],
      answer: 1,
      explanation: 'В 1997 году компьютер IBM Deep Blue выиграл матч у действующего чемпиона мира по шахматам.',
      source: 'https://www.ibm.com/investor/att/pdf/IBM_Annual_Report_1997.pdf',
    },
    {
      question: 'Что нейросеть пытается угадать в игре Google Quick, Draw!?',
      options: ['Мелодию', 'Нарисованный за 20 секунд предмет', 'Ваш следующий поисковый запрос'],
      answer: 1,
      explanation: 'Вы быстро рисуете предмет, а нейросеть угадывает его по наброску. Рисунки также помогают исследовать машинное обучение.',
      source: 'https://www.quickdraw.withgoogle.com/',
    },
    {
      question: 'Что научилась предсказывать система AlphaFold?',
      options: ['Погоду на год вперёд', 'Трёхмерную форму белков', 'Возраст любой звезды'],
      answer: 1,
      explanation: 'AlphaFold предсказывает пространственную структуру белков. Исследователям открыли базу с более чем 200 миллионами таких предсказаний.',
      source: 'https://deepmind.google/science/alphafold/',
    },
    {
      question: 'Где в 1956 году собрались учёные на семинаре, который помог запустить область ИИ?',
      options: ['В Дартмутском колледже', 'В штаб-квартире NASA', 'На киностудии'],
      answer: 0,
      explanation: 'Летний исследовательский семинар в Дартмуте считается одним из отправных событий в истории искусственного интеллекта.',
      source: 'https://home.dartmouth.edu/about/artificial-intelligence-ai-coined-dartmouth',
    },
  ];

  const question = document.querySelector('#wait-quiz-question');
  const options = document.querySelector('#wait-quiz-options');
  const feedback = document.querySelector('#wait-quiz-feedback');
  const progress = document.querySelector('#wait-quiz-progress');
  const next = document.querySelector('#wait-quiz-next');
  let deck = [], position = 0, lastId = -1;

  function shuffle(values) {
    const result = [...values];
    for (let i = result.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [result[i], result[j]] = [result[j], result[i]];
    }
    return result;
  }

  function newRound() {
    deck = shuffle(QUESTIONS.map((_, i) => i));
    if (deck[0] === lastId) [deck[0], deck[1]] = [deck[1], deck[0]];
    position = 0;
    render();
  }

  function render() {
    const item = QUESTIONS[deck[position]];
    lastId = deck[position];
    progress.textContent = `ВОПРОС ${position + 1} / ${deck.length}`;
    question.textContent = item.question;
    options.replaceChildren();
    feedback.hidden = true;
    feedback.replaceChildren();
    next.hidden = true;
    next.innerHTML = position === deck.length - 1 ? 'Ещё вопросы <span aria-hidden="true">↻</span>' : 'Следующий вопрос <span aria-hidden="true">→</span>';

    shuffle(item.options.map((label, originalIndex) => ({ label, originalIndex }))).forEach(({ label, originalIndex }) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'wait-quiz-option';
      button.textContent = label;
      button.addEventListener('click', () => choose(item, originalIndex));
      options.append(button);
    });
  }

  function choose(item, choice) {
    if (!feedback.hidden) return;
    const correct = choice === item.answer;
    for (const button of options.children) {
      button.disabled = true;
      if (button.textContent === item.options[item.answer]) button.classList.add('is-correct');
      else if (button.textContent === item.options[choice]) button.classList.add('is-wrong');
    }
    feedback.className = `wait-quiz-feedback ${correct ? 'is-correct' : 'is-wrong'}`;
    const title = document.createElement('strong');
    title.textContent = correct ? 'Точно! ' : 'Почти! ';
    const detail = document.createElement('span');
    detail.textContent = item.explanation;
    const source = document.createElement('a');
    source.href = item.source;
    source.target = '_blank';
    source.rel = 'noopener noreferrer';
    source.textContent = 'Источник факта ↗';
    feedback.append(title, detail, source);
    feedback.hidden = false;
    next.hidden = false;
  }

  next.addEventListener('click', () => {
    if (position === deck.length - 1) newRound();
    else { position++; render(); }
  });

  window.waitQuiz = {
    start: newRound,
    stop: () => { feedback.hidden = true; },
  };
})();
