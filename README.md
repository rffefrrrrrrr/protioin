# بوت حماية تيليجرام

هذا البوت مصمم لحماية مجموعات تيليجرام باستخدام نظام كابتشا للأعضاء الجدد.

## الميزات
- نظام كابتشا رياضي للأعضاء الجدد.
- طرد الأعضاء الذين لا يحلون الكابتشا في الوقت المحدد.
- أوامر للمطورين والمشرفين.
- استخدام MongoDB لتخزين البيانات.
- دعم الويب هوك (Webhook) للنشر على منصات مثل Render.

## الإعداد والتشغيل

### المتطلبات
- Python 3.8 أو أحدث.
- حساب MongoDB Atlas (أو خادم MongoDB محلي).
- توكن بوت تيليجرام من BotFather.

### التثبيت
1.  استنسخ المستودع:
    ```bash
    git clone <رابط_المستودع_هنا>
    cd telegram-protection-bot
    ```
2.  أنشئ بيئة افتراضية (اختياري ولكن موصى به):
    ```bash
    python3 -m venv venv
    source venv/bin/activate
    ```
3.  ثبت التبعيات:
    ```bash
    pip install -r requirements.txt
    ```

### متغيرات البيئة
يجب تعيين متغيرات البيئة التالية:

-   `BOT_TOKEN`: توكن البوت الخاص بك من BotFather.
-   `MONGO_URI`: رابط اتصال MongoDB الخاص بك (على سبيل المثال، `mongodb+srv://user:pass@cluster.mongodb.net/?retryWrites=true&w=majority`).
-   `WEBHOOK_URL`: عنوان URL العام لتطبيقك مع مسار الويب هوك (على سبيل المثال، `https://your-app-name.onrender.com/webhook`).
-   `PORT`: المنفذ الذي سيستمع عليه تطبيق الويب (على سبيل المثال، `8080` أو `10000` لمنصات مثل Render).

**مثال لملف `.env` (للتشغيل المحلي):**
```
BOT_TOKEN=YOUR_BOT_TOKEN_HERE
MONGO_URI=YOUR_MONGODB_CONNECTION_STRING
WEBHOOK_URL=https://your-domain.com/webhook # أو استخدم ngrok للتشغيل المحلي
PORT=8080
```

### التشغيل المحلي (باستخدام الويب هوك)

1.  تأكد من تعيين متغيرات البيئة كما هو موضح أعلاه.
2.  شغل البوت:
    ```bash
    python3 main.py
    ```
    **ملاحظة:** للتشغيل المحلي باستخدام الويب هوك، ستحتاج إلى أداة مثل `ngrok` لإنشاء نفق (tunnel) لـ `WEBHOOK_URL` الخاص بك.

### التشغيل المحلي (باستخدام الاستقصاء - Polling) - للاختبار فقط

إذا كنت ترغب في اختبار البوت محليًا دون إعداد ويب هوك، يمكنك تعديل ملف `main.py` مؤقتًا:

1.  في دالة `main()`، استبدل:
    ```python
    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path="webhook",
        webhook_url=WEBHOOK_URL
    )
    ```
    بـ:
    ```python
    application.run_polling()
    ```
2.  شغل البوت:
    ```bash
    python3 main.py
    ```
    **تذكر إعادة الكود إلى `run_webhook` قبل النشر على Render.**

### النشر على Render

1.  تأكد من أن ملف `main.py` يستخدم `application.run_webhook`.
2.  قم برفع مشروعك إلى مستودع GitHub.
3.  في Render، قم بإنشاء خدمة ويب جديدة (Web Service).
4.  اربطها بمستودع GitHub الخاص بك.
5.  في إعدادات البيئة (Environment Variables) لخدمة Render، أضف:
    -   `BOT_TOKEN`
    -   `MONGO_URI`
    -   `WEBHOOK_URL`: يجب أن يكون هذا هو عنوان URL الذي توفره Render لتطبيقك، متبوعًا بـ `/webhook` (على سبيل المثال، `https://your-app-name.onrender.com/webhook`).
    -   `PORT`: Render عادةً ما توفر هذا المتغير تلقائيًا (عادةً 10000). إذا لم يكن موجودًا، قم بتعيينه إلى `10000`.
6.  تأكد من أن أمر البناء (Build Command) هو `pip install -r requirements.txt`.
7.  تأكد من أن أمر البدء (Start Command) هو `python3 main.py`.
8.  انشر الخدمة. بعد النشر، ستحتاج إلى التأكد من أن تيليجرام يعرف `WEBHOOK_URL` الصحيح. يقوم البوت تلقائيًا بتعيين الويب هوك عند التشغيل، لذا يجب أن يعمل بشكل صحيح بمجرد بدء تشغيل الخدمة على Render.

