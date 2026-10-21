"""dream_lang.py — the word lists and patterns of the languages dream.py reads: stopwords, function words of
unsegmented scripts, weekdays, months, negations, Russian endings, words of one-off events, dates in words.

Data only, no settings: what a household adds (`agent_names`, `extra_stopwords`) is joined in dream.py's
`configure()`. Moved out of dream.py unchanged in 3.0; dream.py loads it by path, like dream_yaml.py, and
re-exports every name, so `dream.RU_STOP` and the rest work as before. It needs nothing but `re`.
"""
import re


RU_STOP = set("""и в во не на я с со что а то как это он она они мы вы ты по из за к у о об от
для же бы ли так уже или но да нет там тут вот про при над под без через между есть быть был
была было были будет если когда чтобы тоже чтоб этот эта эти того этом всё все ещё уже него неё
их его её им ей мне меня тебя нас вам нам свой своя свои очень более менее как-то кто-то что-то
надо нужно можно сейчас потом тогда чтобы который которая которые этого этой этим была будет
сообщение сообщения ветка ветке чат чате топик тред тебе меня себе свои
привет коротко напомни запиши сделай расскажи покажи какие какой какая пожалуйста спасибо
твоего твоей твоих твоим твоими моего моей моих моими нашего нашей наших вашего вашей ваших
поэтому потому почему зачем одним одной словом может могут можешь пришли ответь посмотри
проверь напиши давай давайте
только просто конечно наверное например обязательно получается кажется значит вообще
совсем именно понятно видимо похоже ладно хорошо отлично понял поняла помню помнишь
думаю считаю знаю хочу хотел хотела будем сегодня завтра вчера утром вечером""".split())
# Dates in words: never a topic, and two of them ("12 сентября … завтра") were
# enough for two unrelated texts to "corroborate" each other. Kept apart from
# RU_STOP because the store-duplicate check must NOT ignore them — "12 сентября"
# and "12 октября" are different facts.
RU_TEMPORAL_STOP = set("""сегодня завтра вчера
января февраля марта апреля августа сентября октября ноября декабря""".split())
# Negations and comparatives are stopwords for corroboration (they are not a
# topic) but MUST stay significant for the store-duplicate check: «<person> ест
# острое» and «<person> не ест острое» are opposite facts, and ignoring «не»
# collapsed them into one (external review, 2026-10-07).
RU_POLARITY_STOP = set("""не нет без ни никогда нельзя
более менее больше меньше""".split())
RU_STOP |= RU_TEMPORAL_STOP
# The additions of 2026-10-02 (possessives, connectives, imperatives addressed to
# the agent, dates in words) come from the live top of "emerging themes": none
# of them is a topic.

# Latin stopwords (the tokenizer only keeps len>=4, shorter ones drop anyway).
# Platform words mirror RU_STOP (chat/thread/topic/message): otherwise any
# message with "telegram"+"thread" corroborates any routing fact (48 false
# mentions on live data). chat_id is a separate token and is not affected.
EN_STOP = set("""that this with from your have will just like what when then than they them
some more very much been does were about into over only also which would could should there
here please today okay yes
telegram thread threads topic topics chat chats group groups message messages channel
https http www""".split())

# The other languages the holographic store reads (German, Spanish, French, Portuguese, Italian, Dutch, Turkish,
# Polish, Indonesian): function words of four letters and more — shorter ones never become tokens. Without them two
# unrelated Polish lines met on «dobra» + «wieczorem», two Dutch ones on «gaat» + «naar» (2026-10-13). Words that
# are also English content words («door», «data», «solo», «fare») are left out.
OTHER_STOP = set("""aber alle allem allen aller alles also andere anderen auch beim bereits bitte bist bisschen dabei
dafür daher damit dann darauf darum dass dein deine deinem deinen deiner denn dessen dich dies diese diesem diesen
dieser dieses doch dort durch eine einem einen einer eines etwas euch euer eure für gegen gibt habe haben hast hatte
hätte hier hinter ihm ihn ihnen ihre ihrem ihren ihrer immer jede jedem jeden jeder jedes jetzt kann kannst können
könnte machen macht mein meine meinem meinen meiner mich muss müssen nach noch oder schon sehr sein seine seinem
seinen seiner seit sich sind soll sollen sollte über unser unsere unter viel vielleicht warum weil welche welchen
welcher wenn werden wieder wird wirklich wurde würde zwischen danke genau gerade
algo algunos ahora antes aquí aquel así bien cada casi como contra cual cuál cuando cuándo cuánto desde después donde
dónde durante ella ellas ellos entonces entre eran eres esta está están estar estas este esto estos fuera gracias
hace hacer hasta hola mismo mucho nosotros nuestra nuestro otra otro para pero poco porque puede pueden quien quién
sido sobre sólo somos también tanto tener tengo tiene tienen todo todos usted vamos vale será siempre esos esas
bueno
alors après aussi autre avant avec avoir beaucoup bonjour cela celle celui cette chez comme comment dans depuis donc
elle elles encore être fait faire faut leur leurs mais merci même notre nous parce pendant peut pour pourquoi quand
quel quelle quelque sera serait sont sous suis tout toute toutes tous très vous votre vers voilà ceci ceux
agora ainda algum alguma aqui assim até então essa esse está estão estou isso isto mesmo muito nossa nosso obrigado
obrigada onde outra outro pela pelo pode porquê qual quem também têm tenho tudo você vocês sempre
anche ancora avere bene cosa così dalla dalle dallo degli della delle dello dove dopo ecco essere grazie hanno
loro mentre molto nella nelle nello nostro nostra ogni allora perché però poco quale quanto quello quella questo
questa sono stato stata tanto tutti tutto vostro ciao fino
alle alles altijd ander andere omdat beetje bijna daar daarom deze doen dus eens hebben heeft iets jouw kunnen maar
moet moeten naar onze waar wanneer waarom welke willen worden wordt zijn zich zou zullen alsjeblieft bedankt gaan gaat
ancak artık bana beni benim bile biraz bize bizim bunu bunun böyle diye için kadar nasıl neden nerede niye olan
olarak oldu olur onun senin sizin şimdi sonra önce lütfen teşekkürler teşekkür tamam evet gibi
albo bardzo będzie było była były cały coś czemu dlaczego dlatego gdzie jaki jaka jakie jego jest jestem jeszcze
każdy kiedy która które który mamy może można mnie nasz nasza nawet przez również siebie sobie także tego tutaj
tylko wszystko wtedy więc zawsze żeby dziękuję proszę dobra dobrze teraz
adalah akan anda apakah atau bagaimana bahwa banyak bisa boleh dalam dari dengan jadi juga kalau kami kamu karena
kenapa kita mana masih mereka nanti oleh pada para saja sama sangat saya sedang sekali sudah tapi untuk terima kasih
""".split())
# Months and today / tomorrow / yesterday in those languages and in English: not a topic for corroboration (two
# texts dated "November" are not about one thing — a German November dentist visit corroborated a remark about the
# November rain), but significant for the store-duplicate check, like RU_TEMPORAL_STOP.
OTHER_TEMPORAL_STOP = set("""january february march april june july august september october november december
januar februar märz juni juli oktober dezember heute morgen gestern
enero febrero marzo abril mayo junio julio agosto septiembre setiembre octubre noviembre diciembre hoy mañana ayer
janvier février mars avril juin juillet août septembre octobre novembre décembre aujourd demain hier
janeiro fevereiro março maio junho julho setembro outubro novembro dezembro hoje amanhã ontem
gennaio febbraio aprile maggio giugno luglio settembre ottobre dicembre oggi domani ieri
januari februari maart augustus vandaag gisteren
ocak şubat mart nisan mayıs haziran temmuz ağustos eylül ekim kasım aralık bugün yarın
styczeń stycznia luty lutego marzec marca kwiecień kwietnia maja czerwiec czerwca lipiec lipca sierpień sierpnia
wrzesień września październik października listopad listopada grudzień grudnia dzisiaj jutro wczoraj
maret juni agustus desember besok kemarin""".split())
# Negations and comparatives: stopwords for corroboration, significant for the store-duplicate check (RU_POLARITY_STOP).
OTHER_POLARITY_STOP = set("""nicht kein keine keinen keiner niemals ohne mehr weniger
nunca nada nadie ningún ninguna jamás menos más
jamais rien personne sans moins plus
nenhum nenhuma mais
niente nessuno senza meno più
niet geen nooit niets zonder meer minder
değil asla
nigdy żaden więcej mniej
tidak bukan belum tanpa lebih kurang""".split())
TEMPORAL_STOP = RU_TEMPORAL_STOP | OTHER_TEMPORAL_STOP
POLARITY_STOP = RU_POLARITY_STOP | OTHER_POLARITY_STOP


# Scripts written without spaces between words. A Chinese, Japanese or Thai sentence is one regex "word", so two
# texts about the same thing shared no token and nothing in these languages was ever corroborated (0 of 30
# questions found their fact, 2026-10-13). Such runs are compared by character n-grams instead, as the holographic
# store compares them: pairs for Chinese/Japanese and Korean, triples for Thai, Lao, Khmer and Myanmar, whose
# characters are letters. Korean is written with spaces, but its particles stick to the word (레나는 = Lena + topic).
_HAN_KANA = "々぀-ヿㇰ-ㇿ㐀-䶿一-鿿豈-﫿ｦ-ﾟ"
_HANGUL = "가-힯"
_SEA = "฀-໿က-႟ក-៿"
UNSEGMENTED_RE = re.compile(f"[{_HAN_KANA}]+|[{_HANGUL}]+|[{_SEA}]+")
_SEA_RE = re.compile(f"[{_SEA}]")
_HIRAGANA_RE = re.compile("[぀-ゟ]+")
_HAN_RE = re.compile("[々㐀-䶿一-鿿豈-﫿]")
# Chinese function characters (particles, pronouns, question words, auxiliaries) and Japanese particles: a pair
# made only of them (什么, 我们, には) carries no topic and is dropped; a pair with one of them counts half.
CJK_FUNCTION = frozenset("的是了吗呢吧啊呀哦嗯么怎什哪谁几多少在有和与及或就都也还要会能可以把被给让对为从到这那你我他她它们个一不没很太"
                         "該應些嘛們這個嗎麼還讓從對為會說說来來裡里"
                         "はがをにのでともへやかねよなだ")
# Thai function words: a triple inside one of them carries no topic.
TH_FUNCTION = sorted(set("""ของ ที่ และ ใน กับ ไม่ ได้ จะ แล้ว ไหม อะไร ยังไง อย่างไร คือ เป็น มี ให้ ครับ ค่ะ คะ นะ เลย ด้วย
    แบบ ไหน เท่าไหร่ กี่ ว่า ก็ หรือ แต่ จาก ถึง เรา ฉัน ผม คุณ เขา นี้ นั้น หน่อย ควร วันนี้ พรุ่งนี้ เมื่อวาน""".split()),
                     key=len, reverse=True)
# Whole runs that are chat fillers ("ok", "thanks", "yes"): no n-grams at all.
FILLER_RUNS = frozenset("""はい うん ええ いいえ 了解 了解です りょうかい オーケー ありがとう ありがとうございます お願い
    お願いします よろしく そう そうです わかった わかりました 好的 好 谢谢 謝謝 没问题 對 对 是的 嗯嗯 哈哈 哈哈哈
    네 예 응 아니 아니요 좋아 좋아요 그래 그래요 알겠어 알겠어요 알겠습니다 오케이 고마워 고마워요 감사 감사합니다
    ครับ ค่ะ คะ นะ จ้ะ จ้า โอเค ได้ ได้เลย ใช่ ขอบคุณ ขอบคุณครับ ขอบคุณค่ะ ขอบคุณมาก""".split())


# Days of the week are long words in most languages (Dienstag, понедельник, niedziela) but never rare: a weekday
# still counts as one shared word, never as a rare one that is a mention alone.
WEEKDAY_PREFIXES = tuple("""понедельник вторник среда среду среды четверг пятниц суббот воскресен
monday tuesday wednesday thursday friday saturday sunday
montag dienstag mittwoch donnerstag freitag samstag sonnabend sonntag
lunes martes miércoles jueves viernes sábado domingo lundi mardi mercredi jeudi vendredi samedi dimanche
segunda-feira terça-feira quarta-feira quinta-feira sexta-feira lunedì martedì mercoledì giovedì venerdì sabato domenica
maandag dinsdag woensdag donderdag vrijdag zaterdag zondag pazartesi çarşamba perşembe cumartesi
poniedział wtorek wtorki środa środy środę czwart piąt sobot niedziel senin selasa kamis jumat sabtu""".split())


# Inflectional endings, longest first. Not a morphological analyser — just the
# endings that make one word look like two: «переехали»/«переехал»,
# «Лиссабоне»/«Лиссабон», «транспортные»/«транспорт». A bare prefix cut was too
# crude the other way: at 5 characters «контент» and «контейнер» collapsed into
# one stem (external review, 2026-10-07).
RU_SUFFIXES = (
    "иями", "ями", "ами", "ого", "его", "ому", "ему", "ыми", "ими",
    "ость", "ться", "лась", "лись", "ются", "ешься", "ишься",
    "ах", "ях", "ов", "ев", "ей", "ой", "ый", "ий", "ом", "ем", "ую", "юю",
    "ые", "ие", "ая", "яя", "ее", "ое", "ам", "ям", "ют", "ут", "ат", "ят",
    "ет", "ит", "ла", "ло", "ли", "ся", "сь", "шь",
    "а", "е", "и", "о", "у", "ы", "я", "ю", "й", "ь",
)


NEGATION_WORDS = {w.replace("ё", "е") for w in """не нет без ни никогда нельзя
not no never nor neither none nothing nobody without cannot
doesn don didn isn aren wasn weren hasn haven hadn shouldn wouldn couldn
nicht kein keine keinen keiner niemals ohne nunca nada nadie ningún ninguna jamás
jamais rien personne sans pas non nenhum nenhuma niente nessuno senza
niet geen nooit niets zonder değil asla nie nigdy żaden tidak bukan belum tanpa""".split()}
# A modal carries the negated verb: the word after it is negated too.
_MODALS = set("""должен должна должно должны может могут мог могла могли нужно надо будет будут стал
стала стали хочет хотят умеет can could should would will must may might need needs have has had do does
did be been is are was were want wants going""".split())


# Month names of the other languages the holographic store reads, with the forms a date takes («14 listopada»,
# «14 Kasım'da», «14 de noviembre»); English is here too for the day-first order («14 November»).
OTHER_MONTHS = {}
for _num, _names in enumerate((
        "january jan januar enero janvier janeiro gennaio januari ocak styczeń stycznia",
        "february feb februar febrero février fevereiro febbraio februari şubat luty lutego",
        "march mar märz marzo mars março maart mart marzec marca maret",
        "april apr abril avril aprile nisan kwiecień kwietnia",
        "may mai mayo maio maggio mei mayıs maj maja",
        "june jun juni junio juin junho giugno haziran czerwiec czerwca",
        "july jul juli julio juillet julho luglio temmuz lipiec lipca",
        "august aug agosto août augustus ağustos sierpień sierpnia agustus",
        "september sep sept septiembre setiembre septembre setembro settembre eylül wrzesień września",
        "october oct oktober octubre octobre outubro ottobre ekim październik października",
        "november nov noviembre novembre novembro kasım listopad listopada",
        "december dec dezember diciembre décembre dezembro dicembre aralık grudzień grudnia desember"), 1):
    for _name in _names.split():
        OTHER_MONTHS[_name] = _num
OTHER_MONTHS_RE = "|".join(sorted(map(re.escape, OTHER_MONTHS), key=len, reverse=True))
# A day before the month: «14 November», «14. November», «14th of November», «14 de noviembre (de 2026)».
DAY_MONTH_RE = re.compile(
    rf"\b(\d{{1,2}})(?:st|nd|rd|th|er|º|\.)?\s+(?:de\s+|of\s+)?({OTHER_MONTHS_RE})\b(?:\.?,?\s+(?:de\s+)?(\d{{4}}))?",
    re.IGNORECASE)
# Chinese / Japanese 11月14日 (2026年11月14日), Korean 11월 14일: no \b — the digits touch the characters.
CJK_DATE_RE = re.compile(r"(?:(\d{4})\s*年\s*)?(\d{1,2})\s*月\s*(\d{1,2})\s*[日号號]")
KO_DATE_RE = re.compile(r"(?:(\d{4})\s*년\s*)?(\d{1,2})\s*월\s*(\d{1,2})\s*일")
# Month names as markers `month:MM`, so "the 12th of May" and "the 12th of June" differ. Whole words only: the
# old prefixes made «мама», «машина», «Марина», "maybe" and "market" months too. Words that are also ordinary
# ones ("may", Spanish "mar", the names Maja and Mei) count only next to a day number, i.e. inside a date.
RU_MONTH_WORD_RE = re.compile(
    r"(?<!\w)(январ|феврал|апрел|июн|июл|сентябр|октябр|ноябр|декабр)[ьяею]?(?!\w)"
    r"|(?<!\w)(март|август)[ае]?(?!\w)|(?<!\w)(ма)[йяе](?!\w)", re.IGNORECASE)
RU_MONTH_NUM = {"январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6, "июл": 7, "август": 8,
                "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12}
AMBIGUOUS_MONTHS = frozenset("may mar mai maj maja mart mei jan sep dec".split())


# A day of the week is as much a "number" as a month: "ballet moved to Tuesdays" against the entry "ballet on Mondays"
# is an update, and without a marker the two shared every word, so the fuzzy dedupe swallowed the move as "already in
# memory" (audit 2026-10-13). Whole-word prefixes, longest first ("cumartesi" before "cuma"); words that are also
# ordinary ones are left out (Turkish "pazar" is a market, Indonesian "minggu" a week).
_WEEKDAY_NAMES = (  # a cell of five letters and more is a prefix, a shorter one or one ending in "=" a whole word
    # Russian by its forms: "среда" is also an environment ("рабочая среда", "в среде"), "суббот…" a субботник,
    # "воскресен…" a resurrection, "Пятницкая" a street — only the accusative and plural forms of среда count
    "понедельник вторник среду=|средам=|средами= четверг пятница=|пятницу=|пятницы=|пятнице=|пятницам=|пятницами= "
    "суббота=|субботу=|субботы=|субботе=|субботам=|субботами= воскресенье=|воскресенья=|воскресенью=|воскресеньям=|"
    "воскресеньями=",
    "monday tuesday wednesday thursday friday saturday sunday",
    "montag dienstag mittwoch donnerstag freitag samstag|sonnabend sonntag",
    "lunes martes miercoles jueves viernes sabado domingo",
    "lundi mardi mercredi jeudi vendredi samedi dimanche",
    "segunda-feira terca-feira quarta-feira quinta-feira sexta-feira sabado domingo",
    "lunedi martedi mercoledi giovedi venerdi sabato domenica",
    "maandag dinsdag woensdag donderdag vrijdag zaterdag zondag",
    "pazartesi sali|salilari carsamba persembe cuma|cumalari cumartesi -",
    "poniedzial wtorek|wtork sroda=|srode=|srody=|srodach|srodami|srodzie czwart piatek|piatk sobot niedziel",  # srodek: middle
    "senin selasa rabu kamis jumat sabtu -",
)
_WEEKDAY_PREFIXES_NUM = sorted({(p, n) for row in _WEEKDAY_NAMES for n, cell in enumerate(row.split(), 1)
                                for p in cell.split("|") if p != "-"}, key=lambda pn: -len(pn[0]))
_WEEKDAY_WORD_RE = re.compile(r"(?<![\w-])[^\W\d_][\w-]*", re.UNICODE)
_CJK_WEEKDAY_RE = re.compile(r"(?:星期|礼拜|禮拜|周|週)([一二三四五六日天])|([月火水木金土日])曜|([월화수목금토일])요일")
_CJK_WEEKDAY_NUM = {**dict(zip("一二三四五六", range(1, 7))), "日": 7, "天": 7,
                    **dict(zip("月火水木金土", range(1, 7))), **dict(zip("월화수목금토일", range(1, 8)))}


MONTHS_RE = r"январ[яе]|феврал[яе]|март[ае]?|апрел[яе]|ма[йяе]|июн[яе]|июл[яе]|август[ае]?|сентябр[яе]|октябр[яе]|ноябр[яе]|декабр[яе]"
THAI_MONTHS_RE = (r"มกราคม|กุมภาพันธ์|มีนาคม|เมษายน|พฤษภาคม|มิถุนายน|กรกฎาคม|สิงหาคม|"
                  r"กันยายน|ตุลาคม|พฤศจิกายน|ธันวาคม|ม\.ค\.|ก\.พ\.|มี\.ค\.|เม\.ย\.|"
                  r"พ\.ค\.|มิ\.ย\.|ก\.ค\.|ส\.ค\.|ก\.ย\.|ต\.ค\.|พ\.ย\.|ธ\.ค\.")
# Thai alternatives are deliberately outside `\b`: Thai has no spaces between
# words and `\b` needs a \w/non-\w boundary, so `\bสอบ\b` never matched «สอบ»
# inside «การสอบ» and Thai school messages were never seen as temporary events.
# Words that make a dated fact a one-off event (an appointment, an exam, a meeting, a flight, a deadline, a thing to
# bring). Latin and Cyrillic stems take any ending; Chinese, Japanese, Korean and Thai words are matched anywhere —
# those scripts have no spaces (or glue particles to the word), so a \b never falls where it should.
EPHEMERAL_HINTS_RE = re.compile(
    r"\b(?:тест\w*|контрольн\w*|экзамен\w*|экскурс\w*|дедлайн\w*|"
    r"встреч\w*|созвон\w*|звонок|совещани\w*|собеседовани\w*|"
    r"вылет\w*|рейс\w*|прилёт\w*|прилет\w*|поезд\w*|"
    r"приём\w*|прием\w*|запись\s+к\s+врачу|визит\w*|"
    r"meeting|call|flight|appointment|interview|"
    r"test|exam\w*|deadline\w*|field\s+trip|bring|submit|prepare|"
    r"sight\s+words?|страниц\w*|лист\w*\s+для\s+чтени\w*|"
    r"завтра|сегодня|послезавтра|принести|сдать|подготовить|"
    r"termin|termine|terminen|arzttermin\w*|prüfung\w*|klassenarbeit\w*|klausur\w*|besprechung\w*|abflug|flug|frist|"
    r"abgabe\w*|mitbringen|abgeben|elternabend\w*|"
    r"cita|citas|examen\w*|prueba\w*|reunión|reuniones|vuelo\w*|plazo\w*|entrega\w*|traer|entregar|excursión|"
    r"rendez-vous|contrôle\w*|réunion\w*|vol|échéance\w*|apporter|rendre|sortie\s+scolaire|"
    r"consulta\w*|prova\w*|exame\w*|reunião|reuniões|voo\w*|prazo\w*|levar|trazer|excursão|"
    r"appuntament\w*|esam\w*|verifica\w*|riunion\w*|volo|voli|scadenz\w*|portare|consegna\w*|gita|"
    r"afspra\w*|toets\w*|vergadering\w*|vlucht\w*|meenemen|inleveren|schoolreis\w*|"
    r"randevu\w*|sınav\w*|toplantı\w*|uçuş\w*|teslim\w*|getir\w*|götür\w*|gezi\w*|"
    r"wizyt\w*|sprawdzian\w*|egzamin\w*|kartkówk\w*|spotkani\w*|przynieś\w*|oddać|wycieczk\w*|"
    r"janji|ujian|ulangan|rapat|pertemuan|penerbangan|tenggat|bawa|kumpulkan|karyawisata|"
    r"dentist|(?:to|at|see|visit)\s+(?:the|a)\s+doctor|doctor'?s?\s+(?:appointment|visit)|trip|flies|fly|due|"
    r"стоматолог\w*|к\s+врачу|у\s+врача|врач\s+прид[её]т|вызвать\s+врача|вызов\s+врача|при[её]м\s+врача|утренник\w*|zahnarzt\w*|ausflug\w*|wandertag\w*|"
    r"llevar|dentista|médico|último\s+dia|colloqui\w*|uçağ\w*|uçak|son\s+ödeme|son\s+gün|dentyst\w*|"
    r"wylot\w*|lot\s+do|zebrani\w*|\w*toets\w*|ouderavond\w*|tandarts\w*|jadwal|batas|dikumpulkan)\b"
    r"|(?:สอบ|กำหนดส่ง|ทัศนศึกษา|นำมา|ส่ง|นัด|ประชุม|เที่ยวบิน|"
    r"予約|テスト|試験|会議|打ち合わせ|フライト|締め切り|締切|持って|提出|遠足|"
    r"预约|預約|约了|約了|考试|考試|测验|測驗|会议|會議|航班|截止|带上|帶上|提交|春游|秋游|"
    r"예약|시험|회의|비행|마감|가져|제출|소풍|고사|상담|까지|"
    r"参観|健康診断|検診|出張|の便|歯医者|までに|复查|復查|复诊|復診|体检|體檢|飞|飛|家长会|家長會|之前|以前|"
    r"วัคซีน|บิน|ภายใน)",
    re.IGNORECASE,
)


RU_MONTHS = {"январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6,
             "июл": 7, "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12}
EN_MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
             "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
THAI_MONTHS = {"มกราคม": 1, "กุมภาพันธ์": 2, "มีนาคม": 3, "เมษายน": 4, "พฤษภาคม": 5,
               "มิถุนายน": 6, "กรกฎาคม": 7, "สิงหาคม": 8, "กันยายน": 9, "ตุลาคม": 10,
               "พฤศจิกายน": 11, "ธันวาคม": 12,
               "ม.ค.": 1, "ก.พ.": 2, "มี.ค.": 3, "เม.ย.": 4, "พ.ค.": 5, "มิ.ย.": 6,
               "ก.ค.": 7, "ส.ค.": 8, "ก.ย.": 9, "ต.ค.": 10, "พ.ย.": 11, "ธ.ค.": 12}

ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
RU_DATE_RE = re.compile(rf"\b(\d{{1,2}})\s+({MONTHS_RE})(?:\s+(\d{{4}}))?\b", re.IGNORECASE)
# Month names whole ("Dec", "December"), not any word that starts like one: "mardi 10", "Marina 5", "Mark 3",
# "Jane 14" and "Mary 5" used to be dates (review 2026-10-13). A day may carry its suffix ("Dec 17th").
EN_DATE_RE = re.compile(
    r"\b(january|jan|february|feb|march|mar|april|apr|may|june|jun|july|jul|august|aug|september|sept|sep|"
    r"october|oct|november|nov|december|dec)\b\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?\b", re.IGNORECASE)
THAI_DATE_RE = re.compile(rf"(\d{{1,2}})\s+({THAI_MONTHS_RE})(?:\s+(\d{{4}}))?")
