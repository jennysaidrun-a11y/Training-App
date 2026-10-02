"""Languages for the worker side: the pages workers see, the lesson player and the
survey. Manager pages stay in English. Lessons carry their own translations
(`translations:` in the lesson file); a lesson without one shows in English.

These UI strings were translated for the app, not by a certified translator:
have a native speaker check them before relying on them."""

LANGS = [("en", "English"), ("es", "Español"), ("zh", "中文"), ("vi", "Tiếng Việt"), ("ar", "العربية")]
CODES = [c for c, _ in LANGS]
RTL = {"ar"}
COOKIE = "ub_lang"

S = {
    # nav and home
    "learn": {"en": "Learn", "es": "Aprender", "zh": "学习", "vi": "Học", "ar": "تعلّم"},
    "new_here": {"en": "New here? Make your account", "es": "¿Es nuevo? Cree su cuenta", "zh": "新员工？创建您的账户",
                 "vi": "Mới đến? Tạo tài khoản", "ar": "جديد هنا؟ أنشئ حسابك"},
    "join_title": {"en": "Make your account", "es": "Cree su cuenta", "zh": "创建您的账户", "vi": "Tạo tài khoản", "ar": "أنشئ حسابك"},
    "join_intro": {"en": "A manager checks and turns on your account. Then tap your name on the first page to start.",
                   "es": "Un gerente revisa y activa su cuenta. Luego toque su nombre en la primera página para empezar.",
                   "zh": "经理会审核并开通您的账户。然后在首页点击您的名字开始。",
                   "vi": "Quản lý sẽ kiểm tra và mở tài khoản cho bạn. Sau đó chạm vào tên bạn ở trang đầu để bắt đầu.",
                   "ar": "يراجع المدير حسابك ويفعّله. بعدها اضغط على اسمك في الصفحة الأولى للبدء."},
    "your_name": {"en": "Your full name", "es": "Su nombre completo", "zh": "您的全名", "vi": "Họ và tên", "ar": "اسمك الكامل"},
    "your_job": {"en": "Your job", "es": "Su puesto", "zh": "您的岗位", "vi": "Công việc của bạn", "ar": "وظيفتك"},
    "pick_pin": {"en": "Pick a 4-digit PIN", "es": "Elija un PIN de 4 dígitos", "zh": "设置4位PIN码", "vi": "Chọn mã PIN 4 số",
                 "ar": "اختر رقمًا سريًا من 4 أرقام"},
    "pin_again": {"en": "Type it again", "es": "Escríbalo otra vez", "zh": "再输入一次", "vi": "Nhập lại", "ar": "اكتبه مرة أخرى"},
    "join_send": {"en": "Send to a manager", "es": "Enviar a un gerente", "zh": "发送给经理", "vi": "Gửi cho quản lý", "ar": "أرسل إلى المدير"},
    "join_done": {"en": "Thanks! A manager will turn on your account. When they do, your name shows on the first page.",
                  "es": "¡Gracias! Un gerente activará su cuenta. Cuando lo haga, su nombre aparecerá en la primera página.",
                  "zh": "谢谢！经理会开通您的账户。开通后，您的名字会出现在首页。",
                  "vi": "Cảm ơn! Quản lý sẽ mở tài khoản cho bạn. Khi đó, tên bạn sẽ hiện ở trang đầu.",
                  "ar": "شكرًا! سيفعّل المدير حسابك. عندها يظهر اسمك في الصفحة الأولى."},
    "join_bad_name": {"en": "Type your name.", "es": "Escriba su nombre.", "zh": "请输入您的名字。", "vi": "Hãy nhập tên của bạn.", "ar": "اكتب اسمك."},
    "join_bad_role": {"en": "Pick your job.", "es": "Elija su puesto.", "zh": "请选择您的岗位。", "vi": "Hãy chọn công việc.", "ar": "اختر وظيفتك."},
    "join_bad_pin": {"en": "The PIN must be 4 digits, typed the same way twice.", "es": "El PIN debe tener 4 dígitos, escrito igual dos veces.",
                     "zh": "PIN码必须是4位数字，两次输入相同。", "vi": "Mã PIN phải có 4 số, nhập giống nhau hai lần.",
                     "ar": "يجب أن يكون الرقم السري 4 أرقام، مكتوبًا بنفس الطريقة مرتين."},
    "join_bad_full": {"en": "Too many sign-ups are waiting. Ask a manager.", "es": "Hay demasiadas solicitudes esperando. Pregunte a un gerente.",
                      "zh": "等待审核的申请太多。请询问经理。", "vi": "Có quá nhiều yêu cầu đang chờ. Hãy hỏi quản lý.",
                      "ar": "هناك طلبات كثيرة بانتظار المراجعة. اسأل المدير."},
    "manager_signin": {"en": "Manager sign-in", "es": "Acceso de gerentes", "zh": "经理登录", "vi": "Quản lý đăng nhập",
                       "ar": "دخول المديرين"},
    "manager": {"en": "Manager", "es": "Gerente", "zh": "经理", "vi": "Quản lý", "ar": "المدير"},
    "rules": {"en": "Rules", "es": "Reglas", "zh": "法规", "vi": "Quy định", "ar": "القواعد"},
    "sign_out": {"en": "Sign out", "es": "Salir", "zh": "退出", "vi": "Đăng xuất", "ar": "خروج"},
    "language": {"en": "Language", "es": "Idioma", "zh": "语言", "vi": "Ngôn ngữ", "ar": "اللغة"},
    "who_today": {"en": "Who's training today?", "es": "¿Quién se capacita hoy?", "zh": "今天谁来培训？",
                  "vi": "Hôm nay ai học?", "ar": "من سيتدرب اليوم؟"},
    "tap_name": {"en": "Tap your name, then type your 4-digit PIN.", "es": "Toque su nombre y escriba su PIN de 4 dígitos.",
                 "zh": "点击您的名字，然后输入4位PIN码。", "vi": "Chạm vào tên của bạn, rồi nhập mã PIN 4 số.",
                 "ar": "اضغط على اسمك، ثم اكتب رقمك السري المكون من 4 أرقام."},
    "no_one": {"en": "No one here yet", "es": "Todavía no hay nadie", "zh": "还没有人", "vi": "Chưa có ai", "ar": "لا يوجد أحد بعد"},
    "manager_adds": {"en": "A manager adds people on the Manager page.", "es": "Un gerente agrega personas en la página del gerente.",
                     "zh": "经理在经理页面添加人员。", "vi": "Quản lý thêm người ở trang Quản lý.", "ar": "يضيف المدير الأشخاص من صفحة المدير."},
    # sign-in
    "hi": {"en": "Hi, {name}", "es": "Hola, {name}", "zh": "{name}，您好", "vi": "Chào {name}", "ar": "مرحبًا، {name}"},
    "type_pin": {"en": "Type your 4-digit PIN.", "es": "Escriba su PIN de 4 dígitos.", "zh": "请输入4位PIN码。",
                 "vi": "Nhập mã PIN 4 số của bạn.", "ar": "اكتب رقمك السري المكون من 4 أرقام."},
    "forgot_pin": {"en": "Forgot your PIN? Ask a manager to set a new one.", "es": "¿Olvidó su PIN? Pida a un gerente que le ponga uno nuevo.",
                   "zh": "忘记PIN码？请经理为您设置新的。", "vi": "Quên mã PIN? Hãy nhờ quản lý đặt mã mới.",
                   "ar": "نسيت رقمك السري؟ اطلب من المدير تعيين رقم جديد."},
    "pin_wrong": {"en": "That PIN isn't right. Try again.", "es": "Ese PIN no es correcto. Inténtelo de nuevo.",
                  "zh": "PIN码不正确，请重试。", "vi": "Mã PIN không đúng. Hãy thử lại.", "ar": "الرقم السري غير صحيح. حاول مرة أخرى."},
    "pin_locked": {"en": "Too many wrong tries. Wait {m} minutes, or ask your manager to reset your PIN.",
                   "es": "Demasiados intentos incorrectos. Espere {m} minutos o pida a su gerente que restablezca su PIN.",
                   "zh": "错误次数太多。请等待{m}分钟，或请经理重置您的PIN码。",
                   "vi": "Nhập sai quá nhiều lần. Hãy đợi {m} phút, hoặc nhờ quản lý đặt lại mã PIN.",
                   "ar": "محاولات خاطئة كثيرة. انتظر {m} دقائق، أو اطلب من مديرك إعادة تعيين رقمك السري."},
    "no_pin": {"en": "You don't have a PIN yet. Ask your manager to set one for you.",
               "es": "Todavía no tiene un PIN. Pida a su gerente que le asigne uno.",
               "zh": "您还没有PIN码。请让经理为您设置。", "vi": "Bạn chưa có mã PIN. Hãy nhờ quản lý tạo cho bạn.",
               "ar": "ليس لديك رقم سري بعد. اطلب من مديرك تعيين واحد لك."},
    "not_you": {"en": "Not {name}? Go back", "es": "¿No es {name}? Volver", "zh": "不是{name}？返回",
                "vi": "Không phải {name}? Quay lại", "ar": "لست {name}؟ ارجع"},
    "back": {"en": "Back", "es": "Volver", "zh": "返回", "vi": "Quay lại", "ar": "رجوع"},
    # my lessons
    "to_go": {"en": "{n} lessons to go", "es": "Faltan {n} lecciones", "zh": "还有{n}节课", "vi": "Còn {n} bài học",
              "ar": "متبقي {n} دروس"},
    "one_to_go": {"en": "1 lesson to go", "es": "Falta 1 lección", "zh": "还有1节课", "vi": "Còn 1 bài học", "ar": "متبقي درس واحد"},
    "caught_up": {"en": "All caught up!", "es": "¡Todo al día!", "zh": "全部完成！", "vi": "Đã học xong hết!", "ar": "أنهيت كل شيء!"},
    "n_of_t_done": {"en": "{d} of {t} done", "es": "{d} de {t} completadas", "zh": "已完成{d}/{t}", "vi": "Đã xong {d}/{t}",
                    "ar": "تم إنجاز {d} من {t}"},
    "start": {"en": "Start", "es": "Empezar", "zh": "开始", "vi": "Bắt đầu", "ar": "ابدأ"},
    "state_due": {"en": "To do", "es": "Pendiente", "zh": "待完成", "vi": "Cần học", "ar": "مطلوب"},
    "state_updated": {"en": "Updated: retake", "es": "Actualizada: repetir", "zh": "已更新：请重学", "vi": "Đã cập nhật: học lại",
                      "ar": "تم التحديث: أعد الدرس"},
    "state_refresh": {"en": "Yearly refresher", "es": "Repaso anual", "zh": "年度复习", "vi": "Ôn tập hằng năm", "ar": "مراجعة سنوية"},
    "done_on": {"en": "Done {date}", "es": "Completada {date}", "zh": "{date}完成", "vi": "Xong ngày {date}", "ar": "أُنجز {date}"},
    "locked": {"en": "Finish the one before first", "es": "Termine primero la anterior", "zh": "请先完成上一课",
               "vi": "Hãy học xong bài trước", "ar": "أكمل الدرس السابق أولاً"},
    "zoom_picture": {"en": "Zoom in on the picture", "es": "Ampliar la imagen", "zh": "放大图片", "vi": "Phóng to hình", "ar": "تكبير الصورة"},
    "zoom_in": {"en": "Zoom in", "es": "Acercar", "zh": "放大", "vi": "Phóng to", "ar": "تكبير"},
    "zoom_out": {"en": "Zoom out", "es": "Alejar", "zh": "缩小", "vi": "Thu nhỏ", "ar": "تصغير"},
    "close": {"en": "Close", "es": "Cerrar", "zh": "关闭", "vi": "Đóng", "ar": "إغلاق"},
    "other_lessons": {"en": "Other lessons", "es": "Otras lecciones", "zh": "其他课程", "vi": "Bài học khác", "ar": "دروس أخرى"},
    "certificate": {"en": "Certificate", "es": "Certificado", "zh": "证书", "vi": "Chứng nhận", "ar": "الشهادة"},
    "in_english": {"en": "", "es": "Esta lección aún no está traducida: se muestra en inglés.",
                   "zh": "本课尚未翻译，以英文显示。", "vi": "Bài học này chưa được dịch: đang hiển thị bằng tiếng Anh.",
                   "ar": "هذا الدرس غير مترجم بعد: يُعرض بالإنجليزية."},
    # lesson intro
    "short_reads": {"en": "Short reads", "es": "Lecturas cortas", "zh": "短文", "vi": "Bài đọc ngắn", "ar": "قراءات قصيرة"},
    "questions": {"en": "Questions", "es": "Preguntas", "zh": "问题", "vi": "Câu hỏi", "ar": "أسئلة"},
    "video": {"en": "Video", "es": "Video", "zh": "视频", "vi": "Video", "ar": "فيديو"},
    "based_on": {"en": "Rules this lesson is based on", "es": "Reglas en las que se basa esta lección", "zh": "本课依据的法规",
                 "vi": "Các quy định làm cơ sở cho bài học", "ar": "القواعد التي يستند إليها هذا الدرس"},
    "last_checked": {"en": "Last checked against these rules on {date}.", "es": "Revisada según estas reglas el {date}.",
                     "zh": "最近一次按这些法规核对：{date}。", "vi": "Kiểm tra lần cuối theo các quy định này ngày {date}.",
                     "ar": "آخر مراجعة وفق هذه القواعد في {date}."},
    "start_lesson": {"en": "Start lesson", "es": "Empezar lección", "zh": "开始上课", "vi": "Bắt đầu học", "ar": "ابدأ الدرس"},
    "preview_only": {"en": "Preview · results aren't saved", "es": "Vista previa · no se guardan resultados", "zh": "预览 · 不保存结果",
                     "vi": "Xem trước · không lưu kết quả", "ar": "معاينة · لا تُحفظ النتائج"},
    # player
    "part_of": {"en": "Part {a} of {b}", "es": "Parte {a} de {b}", "zh": "第{a}部分，共{b}部分", "vi": "Phần {a}/{b}", "ar": "الجزء {a} من {b}"},
    "continue": {"en": "Continue", "es": "Continuar", "zh": "继续", "vi": "Tiếp tục", "ar": "متابعة"},
    "check": {"en": "Check", "es": "Comprobar", "zh": "检查", "vi": "Kiểm tra", "ar": "تحقق"},
    "cant_reach": {"en": "Couldn't reach the app", "es": "No se pudo conectar con la aplicación", "zh": "无法连接应用",
                   "vi": "Không kết nối được ứng dụng", "ar": "تعذر الاتصال بالتطبيق"},
    "check_again": {"en": "Check the connection and press Check again.", "es": "Revise la conexión y presione Comprobar otra vez.",
                    "zh": "请检查网络后再点“检查”。", "vi": "Kiểm tra kết nối rồi bấm Kiểm tra lại.", "ar": "تحقق من الاتصال ثم اضغط تحقق مرة أخرى."},
    "nice_job": {"en": "Nice job!", "es": "¡Muy bien!", "zh": "答对了！", "vi": "Làm tốt lắm!", "ar": "أحسنت!"},
    "not_quite": {"en": "Not quite", "es": "No exactamente", "zh": "不太对", "vi": "Chưa đúng", "ar": "ليس تمامًا"},
    "try_again": {"en": "Try again", "es": "Intentar de nuevo", "zh": "再试一次", "vi": "Thử lại", "ar": "حاول مرة أخرى"},
    "watch": {"en": "Watch", "es": "Ver", "zh": "观看", "vi": "Xem", "ar": "شاهد"},
    "skip_reading": {"en": "Skip to the reading", "es": "Ir a la lectura", "zh": "跳到阅读", "vi": "Chuyển sang phần đọc", "ar": "انتقل إلى القراءة"},
    "quick_check": {"en": "Quick check", "es": "Repaso rápido", "zh": "快速检查", "vi": "Kiểm tra nhanh", "ar": "تحقق سريع"},
    "quiz": {"en": "Quiz", "es": "Examen", "zh": "测验", "vi": "Bài kiểm tra", "ar": "اختبار"},
    "check_read": {"en": "Check what you read", "es": "Compruebe lo que leyó", "zh": "检查您读到的内容", "vi": "Kiểm tra điều bạn vừa đọc",
                   "ar": "تحقق مما قرأته"},
    "saving": {"en": "Saving…", "es": "Guardando…", "zh": "正在保存…", "vi": "Đang lưu…", "ar": "جارٍ الحفظ…"},
    "cant_save": {"en": "Couldn't save your result", "es": "No se pudo guardar su resultado", "zh": "无法保存您的结果",
                  "vi": "Không lưu được kết quả", "ar": "تعذر حفظ نتيجتك"},
    "check_conn": {"en": "Check the connection and try again.", "es": "Revise la conexión e inténtelo de nuevo.", "zh": "请检查网络后重试。",
                   "vi": "Kiểm tra kết nối rồi thử lại.", "ar": "تحقق من الاتصال وحاول مرة أخرى."},
    "complete": {"en": "Lesson complete!", "es": "¡Lección completada!", "zh": "课程完成！", "vi": "Hoàn thành bài học!", "ar": "اكتمل الدرس!"},
    "almost": {"en": "Almost there", "es": "Casi lo logra", "zh": "差一点", "vi": "Gần đạt rồi", "ar": "اقتربت"},
    "saved_cert": {"en": "Saved to your record, with a certificate.", "es": "Guardado en su registro, con un certificado.",
                   "zh": "已保存到您的记录，并附证书。", "vi": "Đã lưu vào hồ sơ của bạn, kèm chứng nhận.", "ar": "تم الحفظ في سجلك مع شهادة."},
    "preview_saved": {"en": "Preview only: nothing was saved.", "es": "Solo vista previa: no se guardó nada.", "zh": "仅预览：未保存任何内容。",
                      "vi": "Chỉ xem trước: không lưu gì.", "ar": "معاينة فقط: لم يُحفظ شيء."},
    "need_pass": {"en": "You need {p}% or more to pass. Take it again; you know the answers now.",
                  "es": "Necesita {p}% o más para aprobar. Hágala de nuevo; ya conoce las respuestas.",
                  "zh": "需要{p}%或以上才能通过。请再学一次，您现在已经知道答案了。",
                  "vi": "Bạn cần đạt {p}% trở lên. Hãy làm lại; giờ bạn đã biết đáp án.",
                  "ar": "تحتاج إلى {p}% أو أكثر للنجاح. أعد الدرس؛ أنت تعرف الإجابات الآن."},
    "score": {"en": "Score", "es": "Puntaje", "zh": "得分", "vi": "Điểm", "ar": "النتيجة"},
    "right_first": {"en": "Right first try", "es": "Correctas al primer intento", "zh": "首次答对", "vi": "Đúng ngay lần đầu",
                    "ar": "صحيحة من أول محاولة"},
    "view_cert": {"en": "View certificate", "es": "Ver certificado", "zh": "查看证书", "vi": "Xem chứng nhận", "ar": "عرض الشهادة"},
    "later": {"en": "Later", "es": "Más tarde", "zh": "稍后", "vi": "Để sau", "ar": "لاحقًا"},
    "send": {"en": "Send", "es": "Enviar", "zh": "提交", "vi": "Gửi", "ar": "إرسال"},
    "survey_kicker": {"en": "Quick survey · anonymous", "es": "Encuesta breve · anónima", "zh": "简短调查 · 匿名",
                      "vi": "Khảo sát nhanh · ẩn danh", "ar": "استبيان قصير · مجهول"},
    "optional_no_name": {"en": "Optional. Please don't write your name.", "es": "Opcional. Por favor no escriba su nombre.",
                         "zh": "选填。请不要写您的名字。", "vi": "Không bắt buộc. Vui lòng không ghi tên.", "ar": "اختياري. من فضلك لا تكتب اسمك."},
    "cant_send": {"en": "Couldn't send it", "es": "No se pudo enviar", "zh": "无法提交", "vi": "Không gửi được", "ar": "تعذر الإرسال"},
    "thanks": {"en": "Thanks!", "es": "¡Gracias!", "zh": "谢谢！", "vi": "Cảm ơn!", "ar": "شكرًا!"},
    "thanks_text": {"en": "Your feedback helps make the next lessons better.", "es": "Sus comentarios ayudan a mejorar las próximas lecciones.",
                    "zh": "您的反馈有助于改进之后的课程。", "vi": "Góp ý của bạn giúp các bài học sau tốt hơn.",
                    "ar": "ملاحظاتك تساعد في تحسين الدروس القادمة."},
    "done": {"en": "Done", "es": "Listo", "zh": "完成", "vi": "Xong", "ar": "تم"},
    "leave": {"en": "Leave the lesson? Your progress in it won't be saved.", "es": "¿Salir de la lección? Su avance no se guardará.",
              "zh": "离开本课？本课进度不会保存。", "vi": "Rời bài học? Tiến độ sẽ không được lưu.", "ar": "مغادرة الدرس؟ لن يُحفظ تقدمك فيه."},
}


def pick(request, who=None):
    """The worker's saved language, else the one picked on this device, else English."""
    if who and who.get("kind") == "w" and who.get("lang") in CODES:
        return who["lang"]
    lang = request.cookies.get(COOKIE)
    return lang if lang in CODES else "en"


def t(key, lang="en", **kw):
    text = S.get(key, {}).get(lang) or S.get(key, {}).get("en", key)
    return text.format(**kw) if kw else text


def table(lang):
    """All strings for one language, for the player script."""
    return {k: v.get(lang) or v["en"] for k, v in S.items()}


def localized(value, lang):
    """A survey field is either text or {lang: text}."""
    if isinstance(value, dict):
        return value.get(lang) or value.get("en", "")
    return value
