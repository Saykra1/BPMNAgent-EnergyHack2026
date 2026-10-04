/* Source-backed AI stories. Add questions here to extend the waiting-room quiz. */
(() => {
  const HISTORY = 'https://www.computerhistory.org/timeline/ai-robotics/';
  const ALPHAGO = 'https://deepmind.google/research/alphago/';
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
    {
      question: 'Что искал ранний робот Элси, похожий на маленькую черепаху?',
      options: ['Умеренный свет', 'Монеты на полу', 'Других роботов'],
      answer: 0,
      explanation: 'Элси двигалась к умеренному свету, избегая слишком яркого света и темноты. Так исследовали поведение машин, которые сами ищут цель.',
      source: HISTORY,
    },
    {
      question: 'Что робот-белка Squee принимал за «орехи»?',
      options: ['Камешки', 'Теннисные мячи', 'Батарейки'],
      answer: 1,
      explanation: 'Робот находил теннисные мячи с помощью световых и контактных датчиков и тащил их в своё «гнездо».',
      source: HISTORY,
    },
    {
      question: 'Что сравнивает человек в предложенной Аланом Тьюрингом «игре в имитацию»?',
      options: ['Письменные ответы человека и машины', 'Скорость двух процессоров', 'Фотографии робота и человека'],
      answer: 0,
      explanation: 'Идея состояла в том, чтобы по письменным ответам попробовать отличить машину от человека.',
      source: HISTORY,
    },
    {
      question: 'Какую задачу решала ранняя программа Logic Theorist?',
      options: ['Рисовала картины', 'Доказывала математические теоремы', 'Переводила песни'],
      answer: 1,
      explanation: 'Logic Theorist искала доказательства математических теорем. Это был один из ранних примеров машинного рассуждения.',
      source: HISTORY,
    },
    {
      question: 'В чём помогала учёным одна из первых экспертных систем — DENDRAL?',
      options: ['В химии: определяла структуру молекул', 'В прогнозе погоды', 'В управлении светофорами'],
      answer: 0,
      explanation: 'DENDRAL применяла правила и знания специалистов, чтобы находить возможную структуру органических молекул.',
      source: HISTORY,
    },
    {
      question: 'В каком мире выполняла команды программа SHRDLU?',
      options: ['В виртуальном мире кубиков и пирамид', 'На карте океана', 'В шахматной партии'],
      answer: 0,
      explanation: 'SHRDLU понимала команды о перемещении геометрических фигур в очень простом виртуальном мире.',
      source: HISTORY,
    },
    {
      question: 'Какими «глазами» робот Shakey изучал помещение?',
      options: ['Только GPS', 'Камерой и датчиками расстояния', 'Нарисованной картой из книги'],
      answer: 1,
      explanation: 'Shakey использовал телевизионную камеру, лазерный дальномер и датчики столкновения, чтобы строить маршрут.',
      source: HISTORY,
    },
    {
      question: 'Данные о чём помогала изучать система LUNAR?',
      options: ['О лунных породах миссии Apollo 11', 'О кольцах Сатурна', 'О земных вулканах'],
      answer: 0,
      explanation: 'LUNAR позволяла геологам задавать вопросы о химическом составе лунных камней и грунта.',
      source: HISTORY,
    },
    {
      question: 'Какое препятствие самостоятельно объехала исследовательская Stanford Cart?',
      options: ['Реку', 'Лестницу', 'Стул'],
      answer: 2,
      explanation: 'В 1979 году тележка пересекла комнату и объехала стоящий на пути стул, используя машинное зрение.',
      source: HISTORY,
    },
    {
      question: 'Каким домашним животным был робот AIBO от Sony?',
      options: ['Кошкой', 'Собакой', 'Попугаем'],
      answer: 1,
      explanation: 'AIBO был робопсом: реагировал на голосовые команды и даже иногда «не слушался» хозяина.',
      source: HISTORY,
    },
    {
      question: 'Что умел человекоподобный робот ASIMO, показанный в 2000 году?',
      options: ['Подниматься по лестнице', 'Летать', 'Погружаться на дно океана'],
      answer: 0,
      explanation: 'ASIMO мог ходить, подниматься по лестнице и менять путь, заметив препятствие.',
      source: HISTORY,
    },
    {
      question: 'Где беспилотный автомобиль Stanley выиграл соревнование в 2005 году?',
      options: ['На пустынной трассе', 'На гоночном треке «Формулы-1»', 'В туннеле метро'],
      answer: 0,
      explanation: 'Stanley самостоятельно проехал внедорожную трассу в пустыне и победил в DARPA Grand Challenge.',
      source: HISTORY,
    },
    {
      question: 'В какой настольной игре прославилась система AlphaGo?',
      options: ['В «Монополии»', 'В го', 'В нардах'],
      answer: 1,
      explanation: 'AlphaGo победила одного из сильнейших игроков в древнюю стратегическую игру го.',
      source: ALPHAGO,
    },
    {
      question: 'Как AlphaGo улучшала игру после изучения партий людей?',
      options: ['Играла против собственных версий', 'Читала комментарии зрителей', 'Меняла правила игры'],
      answer: 0,
      explanation: 'После обучения на партиях экспертов система играла против себя и училась на результатах.',
      source: ALPHAGO,
    },
    {
      question: 'Чем прославился 37-й ход AlphaGo в партии с Ли Седолем?',
      options: ['Он был неожиданным для профессионалов', 'Он нарушил правила', 'Он длился 37 минут'],
      answer: 0,
      explanation: 'Ход казался очень необычным, но помог AlphaGo победить и заставил игроков по-новому взглянуть на го.',
      source: ALPHAGO,
    },
    {
      question: 'Какие три игры освоила AlphaZero, обучаясь через игру с собой?',
      options: ['Го, шахматы и сёги', 'Покер, футбол и тетрис', 'Шахматы, бильярд и домино'],
      answer: 0,
      explanation: 'AlphaZero осваивала го, шахматы и японские шахматы сёги, зная правила и играя сама с собой.',
      source: 'https://deepmind.google/research/alphazero-and-muzero/',
    },
    {
      question: 'Чем MuZero удивила исследователей игр?',
      options: ['Ей не сообщали правила игры заранее', 'Она управляла шахматными фигурами рукой', 'Она играла только за людей'],
      answer: 0,
      explanation: 'MuZero научилась планировать ходы в нескольких играх, не получив заранее их правила.',
      source: 'https://deepmind.google/research/alphazero-and-muzero/',
    },
    {
      question: 'В какой компьютерной игре ИИ AlphaStar достиг уровня грандмастера?',
      options: ['Minecraft', 'StarCraft II', 'Pac-Man'],
      answer: 1,
      explanation: 'AlphaStar достигла уровня грандмастера в StarCraft II, играя в полную версию стратегии.',
      source: 'https://deepmind.google/blog/alphastar-grandmaster-level-in-starcraft-ii-using-multi-agent-reinforcement-learning/',
    },
    {
      question: 'Что генерировала нейросеть WaveNet?',
      options: ['Человеческую речь и музыку', 'Только карты городов', 'Только расписание поездов'],
      answer: 0,
      explanation: 'WaveNet создавала звуковую волну напрямую: так можно синтезировать более естественную речь и даже музыку.',
      source: 'https://deepmind.google/blog/wavenet-a-generative-model-for-raw-audio/',
    },
    {
      question: 'Какую обычную задачу ускорила система AlphaDev?',
      options: ['Сортировку данных', 'Зарядку батареи', 'Печать на принтере'],
      answer: 0,
      explanation: 'AlphaDev нашла более быстрые способы сортировки коротких последовательностей; их включили в стандартную библиотеку C++.',
      source: 'https://deepmind.google/blog/alphadev-discovers-faster-sorting-algorithms/',
    },
    {
      question: 'Что можно сделать в Google Teachable Machine без написания кода?',
      options: ['Обучить простую модель распознавать примеры', 'Построить физический компьютер', 'Изменить погоду'],
      answer: 0,
      explanation: 'Teachable Machine позволяет собрать примеры и обучить простую модель прямо в браузере, без программирования.',
      source: 'https://experiments.withgoogle.com/teachable-machine',
    },
    {
      question: 'Чем помогает инструмент Google AutoDraw?',
      options: ['Подсказывает аккуратные рисунки по вашему наброску', 'Озвучивает любой текст', 'Переводит рисунок в музыку'],
      answer: 0,
      explanation: 'AutoDraw угадывает, что вы пытаетесь нарисовать, и предлагает готовые иллюстрации художников.',
      source: 'https://experiments.withgoogle.com/autodraw',
    },
    {
      question: 'С чьим голосом сравнивает ваше пение эксперимент FreddieMeter?',
      options: ['Фредди Меркьюри', 'Луи Армстронга', 'Йоко Оно'],
      answer: 0,
      explanation: 'FreddieMeter сравнивает тембр, высоту и мелодию исполнения с голосом Фредди Меркьюри.',
      source: 'https://home.experiments.withgoogle.com/freddiemeter',
    },
    {
      question: 'Что делает ИИ в интерактивном Google Doodle, посвящённом Баху?',
      options: ['Дополняет вашу мелодию гармониями в стиле Баха', 'Настраивает гитару', 'Угадывает название оперы'],
      answer: 0,
      explanation: 'Вы сочиняете короткую мелодию, а модель помогает гармонизировать её в стиле Баха.',
      source: 'https://doodles.google/doodle/celebrating-johann-sebastian-bach/',
    },
    {
      question: 'Что показывает эксперимент Bird Sounds с машинным обучением?',
      options: ['Похожие звучания птичьих голосов', 'Маршруты перелётов самолётов', 'Форму птичьих гнёзд'],
      answer: 0,
      explanation: 'Исследователи визуализировали тысячи записей птичьих голосов, чтобы можно было изучать их сходство.',
      source: 'https://experiments.withgoogle.com/ai/bird-sounds/view/',
    },
    {
      question: 'Чьи песни помогают изучать ИИ-инструменты проекта Pattern Radio?',
      options: ['Горбатых китов', 'Канареек', 'Дельфинов'],
      answer: 0,
      explanation: 'Проект Google и NOAA помогает исследовать тысячи часов записей песен горбатых китов.',
      source: 'https://experiments.withgoogle.com/patternradio',
    },
    {
      question: 'Что создаёт система DALL·E по обычному текстовому описанию?',
      options: ['Изображения', 'Запахи', 'Схемы электропроводки'],
      answer: 0,
      explanation: 'DALL·E создаёт изображения по описанию на естественном языке, сочетая объекты и стили.',
      source: 'https://openai.com/index/dall-e/',
    },
    {
      question: 'Что делает модель Whisper с записью речи?',
      options: ['Превращает речь в текст', 'Определяет цвет микрофона', 'Сжимает видеоролик'],
      answer: 0,
      explanation: 'Whisper распознаёт речь и может выдать её текстовую расшифровку.',
      source: 'https://openai.com/index/introducing-chatgpt-and-whisper-apis/',
    },
    {
      question: 'Что помогает марсоходу Perseverance объезжать камни без подсказки с Земли?',
      options: ['Система автономной навигации AutoNav', 'Компас в телефоне', 'Карта лунных кратеров'],
      answer: 0,
      explanation: 'AutoNav выбирает безопасный путь вокруг препятствий, пока марсоход едет по Марсу.',
      source: 'https://www.nasa.gov/missions/mars-2020-perseverance/perseverance-rover/autonomous-systems-help-nasas-perseverance-do-more-science-on-mars-2/',
    },
    {
      question: 'В каком телешоу компьютер IBM Watson победил известных участников?',
      options: ['Jeopardy!', 'The Voice', 'Top Gear'],
      answer: 0,
      explanation: 'В 2011 году Watson выиграл у чемпионов американской викторины Jeopardy!, отвечая на вопросы на естественном языке.',
      source: 'https://www.ibm.com/history/watson-jeopardy',
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
