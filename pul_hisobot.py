"""
Shaxsiy moliya Telegram boti (O'zbekcha) - v2.0 Kengaytirilgan

O'rnatish:
    pip install "python-telegram-bot[job-queue]==21.6" tzdata requests

Ovozli kiritish uchun (ixtiyoriy):
    pip install SpeechRecognition pydub
    + ffmpeg: https://ffmpeg.org/download.html

Ishga tushirish:
    Windows CMD:        set BOT_TOKEN=token && python pul_hisobot.py
    Windows PowerShell: $env:BOT_TOKEN="token"; python pul_hisobot.py
"""

import os, re, csv, io, sqlite3, logging, random, string
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

try:
    import requests as _req
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

try:
    import speech_recognition as sr
    import pydub
    HAS_VOICE = True
except ImportError:
    HAS_VOICE = False

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters,
)

logging.basicConfig(level=logging.INFO)
TOKEN = os.getenv("BOT_TOKEN")
TZ    = ZoneInfo("Asia/Tashkent")
DB    = "pul.db"

# ═══════════════════════════════════════════════════════
#  MENU TUGMALARI
# ═══════════════════════════════════════════════════════
B_EXP,    B_INC    = "➖ Xarajat",       "➕ Daromad"
B_BAL,    B_REP    = "💰 Balans",         "📊 Hisobot"
B_HIS,    B_SEARCH = "📜 Tarix",          "🔍 Qidiruv"
B_ANAL,   B_SETT   = "📈 Tahlil",         "⚙️ Sozlamalar"
B_EXPORT, B_HELP   = "📤 Eksport (CSV)",  "❓ Yordam"

MENU = ReplyKeyboardMarkup(
    [[B_EXP,  B_INC],
     [B_BAL,  B_REP],
     [B_HIS,  B_SEARCH],
     [B_ANAL, B_SETT],
     [B_EXPORT, B_HELP]],
    resize_keyboard=True,
)

EXP_CATS = [
    "🍞 Oziq-ovqat", "🚕 Transport", "🏠 Uy-joy",   "📱 Aloqa",
    "👕 Kiyim",       "💊 Salomatlik", "🎉 Ko'ngil ochar",
    "📚 Ta'lim",      "🎁 Sovg'a",    "🛒 Boshqa",
]
INC_CATS = ["💼 Maosh", "💸 Qo'shimcha", "🎁 Sovg'a", "🏦 Boshqa"]

KEYWORDS = {
    "🍞 Oziq-ovqat":    ["non","go'sht","gosht","osh","ovqat","kafe","restoran","bozor",
                          "market","kofe","choy","tushlik","nonushta","kechki","fastfood","lavash"],
    "🚕 Transport":     ["taksi","yandex","benzin","metro","avtobus","yo'l","yol","gaz","mashina"],
    "🏠 Uy-joy":        ["ijara","kommunal","svet","tok","suv","uy"],
    "📱 Aloqa":         ["internet","telefon","beeline","ucell","uzmobile","mobiuz","paynet"],
    "👕 Kiyim":         ["kiyim","oyoq","krossovka","ko'ylak","shim"],
    "💊 Salomatlik":    ["dori","apteka","shifokor","klinika","tish"],
    "🎉 Ko'ngil ochar": ["kino","o'yin","oyin","konsert","dam"],
    "📚 Ta'lim":        ["kurs","kitob","dars","o'qish","oqish"],
    "💼 Maosh":         ["maosh","oylik","ish haqi"],
}

# ═══════════════════════════════════════════════════════
#  MA'LUMOTLAR BAZASI
# ═══════════════════════════════════════════════════════
def db(sql, args=(), fetch=None):
    con = sqlite3.connect(DB)
    cur = con.execute(sql, args)
    res = None
    if fetch == "one": res = cur.fetchone()
    elif fetch == "all": res = cur.fetchall()
    con.commit(); con.close()
    return res

def init_db():
    # Asosiy jadvallar
    db("""CREATE TABLE IF NOT EXISTS tx(
        id INTEGER PRIMARY KEY AUTOINCREMENT, uid INTEGER, type TEXT,
        amount REAL, cat TEXT, note TEXT, created TEXT)""")
    db("CREATE TABLE IF NOT EXISTS lim(uid INTEGER PRIMARY KEY, amount REAL)")
    # Yangi jadvallar
    db("""CREATE TABLE IF NOT EXISTS cat_limits(
        uid INTEGER, cat TEXT, amount REAL, PRIMARY KEY(uid,cat))""")
    db("""CREATE TABLE IF NOT EXISTS recurring(
        id INTEGER PRIMARY KEY AUTOINCREMENT, uid INTEGER, type TEXT,
        amount REAL, cat TEXT, note TEXT, interval_days INTEGER, next_date TEXT)""")
    db("""CREATE TABLE IF NOT EXISTS reminders(
        uid INTEGER PRIMARY KEY, hour INTEGER, minute INTEGER, weekly INTEGER DEFAULT 0)""")
    db("""CREATE TABLE IF NOT EXISTS family(
        group_id INTEGER PRIMARY KEY AUTOINCREMENT, admin_uid INTEGER, code TEXT UNIQUE)""")
    db("""CREATE TABLE IF NOT EXISTS family_members(
        group_id INTEGER, uid INTEGER, PRIMARY KEY(group_id,uid))""")

# ═══════════════════════════════════════════════════════
#  YORDAMCHI FUNKSIYALAR
# ═══════════════════════════════════════════════════════
def now():
    return datetime.now(TZ)

def fmt(n):
    return f"{n:,.0f}".replace(",", " ") + " so'm"

def gen_code():
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=6))

def period_start(p):
    n = now()
    if p == "today":
        d = n.replace(hour=0, minute=0, second=0, microsecond=0)
    elif p == "week":
        d = (n - timedelta(days=6)).replace(hour=0, minute=0, second=0, microsecond=0)
    elif p == "month":
        d = n.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elif p == "prev":
        first = n.replace(day=1)
        d = (first - timedelta(days=1)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        d = n.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return d.strftime("%Y-%m-%d %H:%M:%S")

def period_end_prev():
    return now().replace(day=1, hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M:%S")

def total(uid, typ, since=None, until=None):
    sql  = "SELECT COALESCE(SUM(amount),0) FROM tx WHERE uid=? AND type=?"
    args = [uid, typ]
    if since: sql += " AND created>=?"; args.append(since)
    if until: sql += " AND created<?";  args.append(until)
    return db(sql, args, "one")[0]

def total_family(uid, typ, since=None, until=None):
    uids = get_family_uids(uid)
    return sum(total(u, typ, since, until) for u in uids)

def get_family_group(uid):
    row = db("SELECT group_id FROM family_members WHERE uid=?", (uid,), "one")
    return row[0] if row else None

def get_family_uids(uid):
    gid = get_family_group(uid)
    if not gid: return [uid]
    rows = db("SELECT uid FROM family_members WHERE group_id=?", (gid,), "all")
    return [r[0] for r in rows] if rows else [uid]

def parse(text):
    """'+50000 non', '-15k taksi', '+2mln' -> (belgi, summa, izoh)"""
    t = re.sub(r"(?<=\d) (?=\d{3}\b)", "", text.strip())
    m = re.match(r"^([+\-])?\s*(\d+(?:[.,]\d+)?)\s*(k|ming|mln|m)?\b\s*(.*)$", t, re.I | re.S)
    if not m: return None
    sign, num, suf, note = m.groups()
    val  = float(num.replace(",", "."))
    mult = {"k":1e3,"ming":1e3,"m":1e6,"mln":1e6}.get((suf or "").lower(), 1)
    return sign, val * mult, note.strip()

def guess_cat(note, cats):
    low = note.lower()
    for cat, words in KEYWORDS.items():
        if cat in cats and any(w in low for w in words):
            return cat
    return None

# ═══════════════════════════════════════════════════════
#  SAQLASH (limit tekshirish bilan)
# ═══════════════════════════════════════════════════════
async def save(update, context, uid, typ, amount, cat, note, reply):
    db("INSERT INTO tx(uid,type,amount,cat,note,created) VALUES(?,?,?,?,?,?)",
       (uid, typ, amount, cat, note, now().strftime("%Y-%m-%d %H:%M:%S")))
    icon = "➖" if typ == "expense" else "➕"
    bal  = total(uid, "income") - total(uid, "expense")
    msg  = f"{icon} {fmt(amount)} — {cat}"
    if note: msg += f" ({note})"
    msg += f"\n💰 Balans: {fmt(bal)}"

    if typ == "expense":
        # Umumiy oylik limit
        row = db("SELECT amount FROM lim WHERE uid=?", (uid,), "one")
        if row and row[0] > 0:
            spent = total(uid, "expense", period_start("month"))
            pct   = spent / row[0] * 100
            if pct >= 100:
                msg += f"\n🚨 Oylik limitdan oshdingiz! ({fmt(spent)} / {fmt(row[0])})"
            elif pct >= 80:
                msg += f"\n⚠️ Limitning {pct:.0f}% ishlatildi ({fmt(spent)} / {fmt(row[0])})"
        # Kategoriya limiti
        crow = db("SELECT amount FROM cat_limits WHERE uid=? AND cat=?", (uid, cat), "one")
        if crow and crow[0] > 0:
            cspent = db("SELECT COALESCE(SUM(amount),0) FROM tx WHERE uid=? AND type='expense' "
                        "AND cat=? AND created>=?", (uid, cat, period_start("month")), "one")[0]
            cpct   = cspent / crow[0] * 100
            if cpct >= 100:
                msg += f"\n🚨 {cat} limiti tugadi! ({fmt(cspent)}/{fmt(crow[0])})"
            elif cpct >= 80:
                msg += f"\n⚠️ {cat}: {cpct:.0f}% ishlatildi"

    await reply(msg, reply_markup=MENU)

# ═══════════════════════════════════════════════════════
#  YORDAM MATNI
# ═══════════════════════════════════════════════════════
HELP_TEXT = (
    "💡 *Qanday ishlaydi — v2.0*\n\n"
    "⚡ Tez kiritish:\n"
    "`15000 non` → xarajat\n"
    "`+2mln maosh` → daromad\n"
    "`-50k taksi` → xarajat\n\n"
    "📌 *Asosiy tugmalar:*\n"
    "💰 Balans | 📊 Hisobot\n"
    "📜 Tarix | 🔍 Qidiruv\n"
    "📈 Tahlil | ⚙️ Sozlamalar\n"
    "📤 Eksport\n\n"
    "📈 *Tahlil:*\n"
    "Grafik, Tejamkorlik, Maslahat, Oylar taqqoslash\n\n"
    "⚙️ *Sozlamalar:*\n"
    "Oylik limit, Kategoriya limiti,\n"
    "Takroriy to'lovlar, Eslatma,\n"
    "Valyuta konvertatsiya, Oilaviy hisob\n\n"
    "🎙️ *Ovozli xabar* — ovoz yuborsa summa aniqlanadi"
)

# ═══════════════════════════════════════════════════════
#  ASOSIY BUYRUQLAR
# ═══════════════════════════════════════════════════════
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    name = update.effective_user.first_name or "Foydalanuvchi"
    # Ismdagi maxsus markdown belgilarini tozalash (xatolik bermasligi uchun)
    safe_name = re.sub(r"([_*\[\]()~`>#+\-=|{}.!])", r"\\\1", name)
    await update.message.reply_text(
        f"Salom, {safe_name}! 👋\nShaxsiy moliya yordamchingiz v2.0\n\n" + HELP_TEXT,
        parse_mode="Markdown", reply_markup=MENU)

async def balance(update, context):
    uid = update.effective_user.id
    inc, exp     = total(uid, "income"), total(uid, "expense")
    m_inc, m_exp = total(uid, "income", period_start("month")), total(uid, "expense", period_start("month"))
    text = (f"💰 *Balans: {fmt(inc - exp)}*\n\n"
            f"Jami daromad: {fmt(inc)}\nJami xarajat: {fmt(exp)}\n\n"
            f"📅 Shu oy:\n➕ {fmt(m_inc)}\n➖ {fmt(m_exp)}")
    # Oilaviy
    if get_family_group(uid):
        fi = total_family(uid, "income", period_start("month"))
        fe = total_family(uid, "expense", period_start("month"))
        text += f"\n\n👨‍👩‍👧 *Oila (shu oy):*\n➕ {fmt(fi)}\n➖ {fmt(fe)}\n💰 {fmt(fi-fe)}"
    await update.message.reply_text(text, parse_mode="Markdown")

async def report_menu(update, context):
    kb = [[InlineKeyboardButton("Bugun",   callback_data="p:today"),
           InlineKeyboardButton("7 kun",   callback_data="p:week"),
           InlineKeyboardButton("Shu oy",  callback_data="p:month")]]
    await update.message.reply_text("Qaysi davr uchun?", reply_markup=InlineKeyboardMarkup(kb))

def build_report(uid, p, group=False):
    since = period_start(p)
    title = {"today":"Bugun","week":"Oxirgi 7 kun","month":"Shu oy"}[p]
    uids  = get_family_uids(uid) if group else [uid]
    inc   = sum(total(u, "income",  since) for u in uids)
    exp   = sum(total(u, "expense", since) for u in uids)
    # Kategoriya
    cat_map = {}
    for u in uids:
        rows = db("SELECT cat,SUM(amount) FROM tx WHERE uid=? AND type='expense' AND created>=? "
                  "GROUP BY cat", (u, since), "all") or []
        for cat, s in rows:
            cat_map[cat] = cat_map.get(cat, 0) + s
    lbl = f" 👨‍👩‍👧 Oila" if group and len(uids) > 1 else ""
    out = f"📊 *{title}{lbl}*\n\n➕ Daromad: {fmt(inc)}\n➖ Xarajat: {fmt(exp)}\n📈 Farq: {fmt(inc-exp)}\n"
    if cat_map:
        out += "\n*Xarajatlar:*\n"
        for cat, s in sorted(cat_map.items(), key=lambda x: -x[1]):
            pct = s / exp * 100 if exp else 0
            bar = "█" * max(1, round(pct / 10))
            out += f"{cat}\n{bar} {fmt(s)} ({pct:.0f}%)\n"
    else:
        out += "\nBu davrda xarajat yo'q."
    return out

async def history(update, context):
    uid  = update.effective_user.id
    rows = db("SELECT id,type,amount,cat,note,created FROM tx WHERE uid=? ORDER BY id DESC LIMIT 10",
              (uid,), "all")
    if not rows:
        await update.message.reply_text("Hali yozuv yo'q."); return
    text, kb = "📜 *Oxirgi yozuvlar*\n\n", []
    for i, (tid, typ, amt, cat, note, created) in enumerate(rows, 1):
        icon  = "➖" if typ == "expense" else "➕"
        text += f"{i}. {icon} {fmt(amt)} — {cat}"
        text += f" ({note})" if note else ""
        text += f"\n    🕒 {created[5:16]}\n"
        kb.append([InlineKeyboardButton(f"🗑 {i}-ni o'chirish", callback_data=f"d:{tid}")])
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

async def export(update, context):
    uid  = update.effective_user.id
    rows = db("SELECT created,type,amount,cat,note FROM tx WHERE uid=? ORDER BY id", (uid,), "all")
    if not rows:
        await update.message.reply_text("Eksport qilish uchun yozuv yo'q."); return
    buf  = io.StringIO()
    w    = csv.writer(buf)
    w.writerow(["Sana","Turi","Summa","Kategoriya","Izoh"])
    for r in rows:
        w.writerow([r[0], "Xarajat" if r[1]=="expense" else "Daromad", int(r[2]), r[3], r[4]])
    data      = io.BytesIO(("\ufeff" + buf.getvalue()).encode("utf-8"))
    data.name = "moliya.csv"
    await update.message.reply_document(data, caption="📤 Barcha yozuvlaringiz")

# ═══════════════════════════════════════════════════════
#  🔍 QIDIRUV
# ═══════════════════════════════════════════════════════
async def search_start(update, context):
    context.user_data["mode"] = "search"
    await update.message.reply_text("🔍 Qidiruv so'zini yozing:\n(masalan: taksi, non, maosh)")

# ═══════════════════════════════════════════════════════
#  📈 TAHLIL MENYUSI
# ═══════════════════════════════════════════════════════
async def tahlil_menu(update, context):
    kb = [
        [InlineKeyboardButton("📊 Grafik",         callback_data="an:chart"),
         InlineKeyboardButton("🏆 Tejamkorlik",    callback_data="an:saving")],
        [InlineKeyboardButton("💡 Maslahat",        callback_data="an:advice"),
         InlineKeyboardButton("📅 Oylar taqqoslash",callback_data="an:compare")],
        [InlineKeyboardButton("👨‍👩‍👧 Oila hisoboti",  callback_data="an:family")],
    ]
    await update.message.reply_text("📈 *Tahlil — nimani ko'rmoqchisiz?*",
                                    parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

def build_chart(uid):
    since = period_start("month")
    rows  = db("SELECT cat,SUM(amount) FROM tx WHERE uid=? AND type='expense' AND created>=? "
               "GROUP BY cat ORDER BY 2 DESC", (uid, since), "all")
    if not rows: return "Bu oy xarajat yo'q."
    tot = sum(r[1] for r in rows)
    out = "📊 *Shu oylik xarajatlar grafigi*\n\n"
    for cat, amt in rows:
        pct    = amt / tot * 100 if tot else 0
        filled = max(1, round(pct / 5))
        bar    = "▓" * filled + "░" * (20 - filled)
        out   += f"{cat}\n`{bar}` {pct:.0f}%\n{fmt(amt)}\n\n"
    out += f"💰 *Jami: {fmt(tot)}*"
    return out

def build_savings_rating(uid):
    ts   = period_start("month")
    ps   = period_start("prev")
    pe   = period_end_prev()
    te   = total(uid, "expense", ts)
    pe_v = total(uid, "expense", ps, pe)
    ti   = total(uid, "income",  ts)
    pi   = total(uid, "income",  ps, pe)
    n    = now()
    day  = max(n.day, 1)
    proj = te / day * 30
    out  = "🏆 *Tejamkorlik reytingi*\n\n"
    if pe_v > 0:
        diff = pe_v - te
        pct  = abs(diff) / pe_v * 100
        if   diff > 0: out += f"✅ O'tgan oyga nisbatan *{fmt(diff)}* kam sarfladingiz! (+{pct:.0f}%)\n\n"
        elif diff < 0: out += f"⚠️ O'tgan oyga nisbatan *{fmt(abs(diff))}* ko'p sarflayapsiz (-{pct:.0f}%)\n\n"
        else:          out += "〰️ O'tgan oy bilan bir xil\n\n"
    out += f"📅 *Shu oy:*\n➕ {fmt(ti)}  ➖ {fmt(te)}\n💰 Tejamkorlik: {fmt(ti-te)}\n\n"
    out += f"📊 *O'tgan oy:*\n➕ {fmt(pi)}  ➖ {fmt(pe_v)}\n💰 Tejamkorlik: {fmt(pi-pe_v)}\n\n"
    out += f"🔮 *Prognoz (oy oxirigacha):* {fmt(proj)}"
    if ti > 0:
        sr  = (ti - te) / ti * 100
        emoji = "✅" if sr >= 20 else ("⚠️" if sr >= 0 else "🚨")
        out += f"\n{emoji} Daromadning {sr:.0f}% tejayapsiz"
    return out

def build_advice(uid):
    since = period_start("month")
    rows  = db("SELECT cat,SUM(amount) FROM tx WHERE uid=? AND type='expense' AND created>=? "
               "GROUP BY cat ORDER BY 2 DESC", (uid, since), "all")
    inc   = total(uid, "income",  since)
    exp   = total(uid, "expense", since)
    if not rows: return "💡 Hali yetarli ma'lumot yo'q. Ko'proq tranzaksiya kiriting!"
    tot      = sum(r[1] for r in rows)
    top, top_amt = rows[0]
    top_pct  = top_amt / tot * 100 if tot else 0
    advices  = []
    if top_pct > 40:
        advices.append(f"⚠️ *{top}* uchun barcha xarajatning {top_pct:.0f}%'ini sarflayapsiz — bu juda ko'p!")
    if inc > 0:
        sr = (inc - exp) / inc * 100
        if   sr < 0:  advices.append(f"🚨 Daromadingizdan {abs(sr):.0f}% ko'p sarflayapsiz!")
        elif sr < 20: advices.append(f"📌 Daromadingizning {sr:.0f}% tejayapsiz. Maqsad: 20%+.")
        else:         advices.append(f"✅ Ajoyib! Daromadingizning {sr:.0f}% ni tejayapsiz.")
    cat_d = dict(rows)
    if cat_d.get("🚕 Transport", 0) / tot * 100 > 20 if tot else False:
        advices.append("🚌 Transport uchun ko'p sarflayapsiz. Metro/avtobus arzonroq.")
    if cat_d.get("🎉 Ko'ngil ochar", 0) / tot * 100 > 15 if tot else False:
        advices.append("🎮 Ko'ngil ochar uchun bir oz ko'p sarflayapsiz.")
    if cat_d.get("🍞 Oziq-ovqat", 0) > 2_000_000:
        advices.append("🏠 Ovqatga ko'p sarflayapsiz. Uyda pishirish tejamkor!")
    if not advices: advices.append("👍 Moliyangiz yaxshi holatda!")
    out = "💡 *Aqlli moliyaviy maslahatlar*\n\n"
    out += "\n\n".join(advices)
    return out

def build_month_compare(uid):
    ts    = period_start("month")
    ps    = period_start("prev")
    pe    = period_end_prev()
    n     = now()
    prev  = (n.replace(day=1) - timedelta(days=1))
    months= {1:"Yanvar",2:"Fevral",3:"Mart",4:"Aprel",5:"May",6:"Iyun",
             7:"Iyul",8:"Avgust",9:"Sentabr",10:"Oktabr",11:"Noyabr",12:"Dekabr"}
    this_lbl = f"{months[n.month]} {n.year}"
    prev_lbl = f"{months[prev.month]} {prev.year}"
    ti = total(uid,"income",  ts);  te = total(uid,"expense", ts)
    pi = total(uid,"income",  ps,pe); pe_v=total(uid,"expense",ps,pe)
    tc = dict(db("SELECT cat,SUM(amount) FROM tx WHERE uid=? AND type='expense' AND created>=? "
                 "GROUP BY cat",(uid,ts),"all") or [])
    pc = dict(db("SELECT cat,SUM(amount) FROM tx WHERE uid=? AND type='expense' AND created>=? AND created<? "
                 "GROUP BY cat",(uid,ps,pe),"all") or [])
    prev_header = "O'tgan oy"
    out  = f"📅 *{this_lbl} vs {prev_lbl}*\n\n"
    out += f"{'':12}{'Bu oy':>14}{prev_header:>14}\n"
    out += "─" * 42 + "\n"
    out += f"➕ Daromad  {fmt(ti):>14}  {fmt(pi):>14}\n"
    out += f"➖ Xarajat  {fmt(te):>14}  {fmt(pe_v):>14}\n"
    out += f"💰 Farq     {fmt(ti-te):>14}  {fmt(pi-pe_v):>14}\n"
    if tc or pc:
        out += "\n*Kategoriyalar:*\n"
        for cat in sorted(set(list(tc)+list(pc)), key=lambda c: -tc.get(c,0)):
            t_v = tc.get(cat, 0); p_v = pc.get(cat, 0)
            arrow = "📈" if t_v > p_v else ("📉" if t_v < p_v else "〰️")
            out += f"{cat}\n  Bu oy: {fmt(t_v)}  |  O'tgan: {fmt(p_v)} {arrow}\n"
    return out

# ═══════════════════════════════════════════════════════
#  ⚙️ SOZLAMALAR MENYUSI
# ═══════════════════════════════════════════════════════
async def settings_menu(update, context):
    kb = [
        [InlineKeyboardButton("🎯 Oylik limit",    callback_data="set:monthlim"),
         InlineKeyboardButton("📂 Kat. limit",     callback_data="set:catlim")],
        [InlineKeyboardButton("🔁 Takroriy to'lov",callback_data="set:recur"),
         InlineKeyboardButton("🔔 Eslatma",         callback_data="set:remind")],
        [InlineKeyboardButton("🌍 Valyuta",         callback_data="set:currency"),
         InlineKeyboardButton("👨‍👩‍👧 Oilaviy hisob", callback_data="set:family")],
    ]
    await update.message.reply_text("⚙️ *Sozlamalar*",
                                    parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

# ═══════════════════════════════════════════════════════
#  🔁 TAKRORIY TO'LOVLAR
# ═══════════════════════════════════════════════════════
async def show_recurring(q, uid):
    rows = db("SELECT id,type,amount,cat,note,interval_days,next_date FROM recurring WHERE uid=?",
              (uid,), "all")
    text = "🔁 *Takroriy to'lovlar*\n\n"
    kb   = []
    if rows:
        imap = {1:"Kunlik",7:"Haftalik",30:"Oylik"}
        for rid, typ, amt, cat, note, ivl, nd in rows:
            icon  = "➕" if typ=="income" else "➖"
            iname = imap.get(ivl, f"{ivl} kunda")
            text += f"{icon} {fmt(amt)} — {cat} ({iname})\n"
            if note: text += f"   📝 {note}\n"
            text += f"   📅 Keyingi: {nd[:10]}\n\n"
            kb.append([InlineKeyboardButton(f"🗑 {fmt(amt)} ({iname}) o'chirish", callback_data=f"rd:{rid}")])
    else:
        text += "Hali takroriy to'lov yo'q.\n\n"
    text += "Yangi qo'shish:"
    kb  += [[InlineKeyboardButton("➕ Daromad", callback_data="ra:income"),
             InlineKeyboardButton("➖ Xarajat", callback_data="ra:expense")],
            [InlineKeyboardButton("❌ Yopish",  callback_data="x")]]
    await q.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

# ═══════════════════════════════════════════════════════
#  🔔 ESLATMA
# ═══════════════════════════════════════════════════════
async def show_reminder(q, uid):
    row  = db("SELECT hour,minute,weekly FROM reminders WHERE uid=?", (uid,), "one")
    if row:
        h, m, w = row
        st = f"✅ Kunlik eslatma: *{h:02d}:{m:02d}*"
        if w: st += " + Haftalik hisobot (Dushanba)"
    else:
        st = "❌ Eslatma o'chirilgan"
    text = f"🔔 *Eslatma sozlamalari*\n\n{st}\n\nO'zgartirish uchun vaqtni yozing:\n`HH:MM` (masalan: 21:00)"
    kb   = []
    if row:
        wl  = "✅ Haftalik hisobot" if row[2] else "⬜ Haftalik hisobot"
        kb += [[InlineKeyboardButton(wl, callback_data="rem:weekly")],
               [InlineKeyboardButton("🗑 Eslatmani o'chirish", callback_data="rem:off")]]
    kb.append([InlineKeyboardButton("❌ Yopish", callback_data="x")])
    await q.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

# ═══════════════════════════════════════════════════════
#  🌍 VALYUTA KONVERTATSIYA
# ═══════════════════════════════════════════════════════
async def show_currency(q):
    text = ("🌍 *Valyuta konvertatsiya*\n\n"
            "Quyidagi formatda yozing:\n"
            "`100 usd`       → 100$ ni so'mga\n"
            "`500000 uzs usd`→ so'mni dollarga\n"
            "`50 eur`        → 50€ ni so'mga\n\n"
            "Qo'llab-quvvatlanuvchi:\nUSD, EUR, RUB, GBP, KZT, CNY")
    kb = [[InlineKeyboardButton("❌ Yopish", callback_data="x")]]
    await q.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

async def do_currency(text, reply_func):
    if not HAS_REQUESTS:
        await reply_func("❌ 'requests' kutubxonasi kerak:\n`pip install requests`", parse_mode="Markdown")
        return
    parts = text.strip().upper().split()
    try:
        if len(parts) == 2:
            amount = float(parts[0].replace(",",".")); fc = parts[1]; tc = "UZS"
        elif len(parts) == 3:
            amount = float(parts[0].replace(",",".")); fc = parts[1]; tc = parts[2]
        else: raise ValueError
    except:
        await reply_func("❌ Format: `100 usd` yoki `500000 uzs usd`", parse_mode="Markdown"); return
    try:
        r    = _req.get(f"https://api.exchangerate-api.com/v4/latest/{fc}", timeout=5)
        data = r.json()
        rate = data["rates"].get(tc)
        if not rate:
            await reply_func(f"❌ {tc} valyutasi topilmadi."); return
        res  = amount * rate
        await reply_func(f"🌍 *{amount:,.2f} {fc}* = *{res:,.2f} {tc}*\nKurs: 1 {fc} = {rate:,.4f} {tc}",
                         parse_mode="Markdown")
    except:
        await reply_func("❌ Internet aloqasi yo'q yoki xizmat ishlamayapti.")

# ═══════════════════════════════════════════════════════
#  👨‍👩‍👧 OILAVIY HISOB
# ═══════════════════════════════════════════════════════
async def show_family(q, uid):
    gid = get_family_group(uid)
    if gid:
        members = db("SELECT uid FROM family_members WHERE group_id=?", (gid,), "all")
        admin   = db("SELECT admin_uid FROM family WHERE group_id=?", (gid,), "one")
        code    = db("SELECT code FROM family WHERE group_id=?", (gid,), "one")[0]
        text    = (f"👨‍👩‍👧 *Oilaviy hisob*\n\n"
                   f"Guruh kodi: `{code}`\n"
                   f"A'zolar: {len(members)} ta\n"
                   f"Admin: {'✅ Siz' if admin and admin[0]==uid else 'Boshqa'}\n\n"
                   f"Yangi a'zo qo'shish uchun kodni ulashing.")
        kb = [[InlineKeyboardButton("🚪 Guruhdan chiqish", callback_data="fam:leave")],
              [InlineKeyboardButton("❌ Yopish", callback_data="x")]]
    else:
        text = "👨‍👩‍👧 *Oilaviy hisob*\n\nOilaviy hisob yarating yoki mavjud guruhga qo'shiling:"
        kb   = [[InlineKeyboardButton("✨ Yangi guruh",        callback_data="fam:create"),
                 InlineKeyboardButton("🔗 Guruhga qo'shilish", callback_data="fam:join")],
                [InlineKeyboardButton("❌ Yopish", callback_data="x")]]
    await q.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

# ═══════════════════════════════════════════════════════
#  🎙️ OVOZLI XABAR
# ═══════════════════════════════════════════════════════
async def on_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not HAS_VOICE:
        await update.message.reply_text(
            "🎙️ Ovozli kiritish uchun:\n"
            "`pip install SpeechRecognition pydub`\n"
            "va ffmpeg o'rnatish kerak.", parse_mode="Markdown")
        return
    await update.message.reply_text("🎙️ Ovoz qabul qilindi, tahlil qilinmoqda...")
    try:
        import tempfile, os as _os
        voice = update.message.voice
        file  = await context.bot.get_file(voice.file_id)
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            await file.download_to_drive(tmp.name); ogg = tmp.name
        wav = ogg.replace(".ogg", ".wav")
        pydub.AudioSegment.from_ogg(ogg).export(wav, format="wav")
        recognizer = sr.Recognizer()
        with sr.AudioFile(wav) as src:
            data = recognizer.record(src)
        text = recognizer.recognize_google(data, language="uz-UZ")
        await update.message.reply_text(f"🎙️ Eshitildi: *{text}*", parse_mode="Markdown")
        update.message.text = text
        await on_text(update, context)
        _os.unlink(ogg); _os.unlink(wav)
    except Exception:
        await update.message.reply_text("❌ Ovozni tushunib bo'lmadi. Matn yozing.")

# ═══════════════════════════════════════════════════════
#  JOB QUEUE — Avtomatik vazifalar
# ═══════════════════════════════════════════════════════
async def job_daily_reminder(context):
    """Har daqiqa tekshiradi — to'g'ri vaqtda eslatadi"""
    n    = now()
    rows = db("SELECT uid,hour,minute FROM reminders", fetch="all") or []
    for uid, h, m in rows:
        if n.hour == h and n.minute == m:
            te = total(uid,"expense",period_start("today"))
            ti = total(uid,"income", period_start("today"))
            bl = total(uid,"income") - total(uid,"expense")
            msg= (f"🔔 *Kunlik eslatma*\n\n📅 {n.strftime('%d.%m.%Y')}\n\n"
                  f"Bugun:\n➕ {fmt(ti)}\n➖ {fmt(te)}\n\n💰 Umumiy balans: {fmt(bl)}\n\n"
                  f"Bugungi xarajatlarni kiritdingizmi? 📝")
            try: await context.bot.send_message(uid, msg, parse_mode="Markdown")
            except: pass

async def job_weekly_report(context):
    """Dushanba kuni haftalik hisobot"""
    if now().weekday() != 0: return
    rows = db("SELECT uid FROM reminders WHERE weekly=1", fetch="all") or []
    for (uid,) in rows:
        try: await context.bot.send_message(uid, build_report(uid,"week"), parse_mode="Markdown")
        except: pass

async def job_recurring(context):
    """Takroriy to'lovlarni bajarish"""
    today = now().strftime("%Y-%m-%d 23:59:59")
    rows  = db("SELECT id,uid,type,amount,cat,note,interval_days FROM recurring WHERE next_date<=?",
               (today,), "all") or []
    for rid, uid, typ, amt, cat, note, ivl in rows:
        db("INSERT INTO tx(uid,type,amount,cat,note,created) VALUES(?,?,?,?,?,?)",
           (uid,typ,amt,cat,note,now().strftime("%Y-%m-%d %H:%M:%S")))
        nd = (now()+timedelta(days=ivl)).strftime("%Y-%m-%d %H:%M:%S")
        db("UPDATE recurring SET next_date=? WHERE id=?", (nd,rid))
        icon = "➕" if typ=="income" else "➖"
        try: await context.bot.send_message(uid, f"🔁 Takroriy to'lov:\n{icon} {fmt(amt)} — {cat}")
        except: pass

# ═══════════════════════════════════════════════════════
#  ASOSIY MATN HANDLERI
# ═══════════════════════════════════════════════════════
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    uid  = update.effective_user.id
    ud   = context.user_data

    # --- Menyu tugmalari ---
    menu = {B_BAL:balance, B_REP:report_menu, B_HIS:history,
            B_EXPORT:export, B_SEARCH:search_start,
            B_ANAL:tahlil_menu, B_SETT:settings_menu}
    if text in menu:
        ud.pop("mode", None)
        return await menu[text](update, context)
    if text == B_HELP:
        return await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")
    if text == B_EXP:
        ud["mode"] = "expense"
        return await update.message.reply_text("Xarajat summasini yozing:\nmasalan: 25000 tushlik")
    if text == B_INC:
        ud["mode"] = "income"
        return await update.message.reply_text("Daromad summasini yozing:\nmasalan: 1.5mln maosh")

    mode = ud.get("mode")

    # --- 🔍 Qidiruv ---
    if mode == "search":
        ud.pop("mode")
        rows = db("SELECT type,amount,cat,note,created FROM tx WHERE uid=? AND note LIKE ? "
                  "ORDER BY id DESC LIMIT 15", (uid, f"%{text}%"), "all")
        if not rows:
            return await update.message.reply_text(f"🔍 '{text}' bo'yicha hech narsa topilmadi.")
        result = f"🔍 *'{text}' natijalari:*\n\n"
        s = 0
        for typ, amt, cat, note, created in rows:
            icon    = "➖" if typ=="expense" else "➕"
            result += f"{icon} {fmt(amt)} — {cat}\n"
            if note: result += f"   📝 {note}\n"
            result += f"   🕒 {created[5:16]}\n\n"
            s      += amt if typ=="income" else -amt
        result += f"*Jami saldo: {fmt(abs(s))}*"
        return await update.message.reply_text(result, parse_mode="Markdown")

    # --- 🔔 Eslatma vaqti ---
    if mode == "reminder":
        m = re.match(r"^(\d{1,2}):(\d{2})$", text)
        if m:
            h, mi = int(m.group(1)), int(m.group(2))
            if 0 <= h <= 23 and 0 <= mi <= 59:
                ud.pop("mode")
                db("INSERT INTO reminders(uid,hour,minute) VALUES(?,?,?) "
                   "ON CONFLICT(uid) DO UPDATE SET hour=?,minute=?", (uid,h,mi,h,mi))
                return await update.message.reply_text(
                    f"🔔 Eslatma o'rnatildi: *{h:02d}:{mi:02d}*",
                    parse_mode="Markdown", reply_markup=MENU)
        return await update.message.reply_text("❌ Format: HH:MM (masalan: 21:00)")

    # --- 🌍 Valyuta ---
    if mode == "currency":
        ud.pop("mode")
        await do_currency(text, update.message.reply_text)
        return

    # --- 📂 Kategoriya limit qiymati ---
    if mode == "cat_limit_val":
        cat = ud.pop("cat_limit_cat", None); ud.pop("mode")
        p   = parse(text)
        if p and cat:
            _, amt, _ = p
            db("INSERT INTO cat_limits(uid,cat,amount) VALUES(?,?,?) "
               "ON CONFLICT(uid,cat) DO UPDATE SET amount=?", (uid,cat,amt,amt))
            msg = f"🎯 {cat} limiti: *{fmt(amt)}*" if amt > 0 else f"🎯 {cat} limiti o'chirildi."
            return await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=MENU)
        return await update.message.reply_text("❌ Noto'g'ri summa.")

    # --- 👨‍👩‍👧 Oila kodi kiritish ---
    if mode == "family_join":
        ud.pop("mode")
        code = text.strip().upper()
        row  = db("SELECT group_id FROM family WHERE code=?", (code,), "one")
        if not row:
            return await update.message.reply_text("❌ Bunday kod topilmadi.")
        if get_family_group(uid):
            return await update.message.reply_text("❌ Avval mavjud guruhdan chiqing.")
        gid = row[0]
        db("INSERT OR IGNORE INTO family_members(group_id,uid) VALUES(?,?)", (gid,uid))
        cnt = db("SELECT COUNT(*) FROM family_members WHERE group_id=?", (gid,), "one")[0]
        return await update.message.reply_text(
            f"✅ Guruhga qo'shildingiz! Jami {cnt} ta a'zo.", reply_markup=MENU)

    # --- 🔁 Takroriy to'lov summasi ---
    if mode == "recurring_add":
        p = parse(text)
        if p:
            _, amt, note = p
            typ  = ud.pop("recurring_type", "expense"); ud.pop("mode")
            cats = INC_CATS if typ=="income" else EXP_CATS
            cat  = guess_cat(note, cats) or ("🏦 Boshqa" if typ=="income" else "🛒 Boshqa")
            ud["recurring_pending"] = (typ, amt, cat, note)
            kb = [[InlineKeyboardButton("📆 Kunlik",  callback_data="ri:1"),
                   InlineKeyboardButton("📅 Haftalik",callback_data="ri:7"),
                   InlineKeyboardButton("🗓 Oylik",   callback_data="ri:30")],
                  [InlineKeyboardButton("❌ Bekor",   callback_data="x")]]
            return await update.message.reply_text(
                f"✅ {fmt(amt)} — {cat}\nQancha vaqtda bir marta?",
                reply_markup=InlineKeyboardMarkup(kb))
        return await update.message.reply_text("❌ Summa noto'g'ri formatda.")

    # --- parse() ---
    p = parse(text)
    if not p:
        return await update.message.reply_text(
            "Tushunmadim 🤔 Summani yozing, masalan: 15000 non", parse_mode="Markdown")
    sign, amount, note = p
    if amount <= 0 and mode != "limit":
        return await update.message.reply_text("Summa 0 dan katta bo'lishi kerak.")

    # --- Limit rejimi ---
    if mode == "limit":
        ud.pop("mode")
        db("INSERT INTO lim(uid,amount) VALUES(?,?) ON CONFLICT(uid) DO UPDATE SET amount=?",
           (uid,amount,amount))
        msg = f"🎯 Oylik limit: {fmt(amount)}" if amount > 0 else "🎯 Limit o'chirildi."
        return await update.message.reply_text(msg, reply_markup=MENU)

    # --- + yoki - bilan kiritilgan: har doim avtomatik ---
    if sign in ("+", "-"):
        typ  = "income" if sign=="+" else "expense"
        ud.pop("mode", None)
        cat  = guess_cat(note, INC_CATS if typ=="income" else EXP_CATS)
        cat  = cat or ("🏦 Boshqa" if typ=="income" else "🛒 Boshqa")
        return await save(update, context, uid, typ, amount, cat, note, update.message.reply_text)

    # --- Belgi yo'q ---
    typ  = ud.pop("mode", "expense")
    cats = INC_CATS if typ=="income" else EXP_CATS
    cat  = guess_cat(note, cats)
    if cat:
        return await save(update, context, uid, typ, amount, cat, note, update.message.reply_text)

    # Kategoriya so'raymiz
    ud["pending"] = (typ, amount, note)
    kb  = [[InlineKeyboardButton(cats[i], callback_data=f"c:{i}")
            for i in range(j, min(j+2, len(cats)))]
           for j in range(0, len(cats), 2)]
    kb.append([InlineKeyboardButton("❌ Bekor qilish", callback_data="x")])
    await update.message.reply_text(f"{fmt(amount)} — kategoriyani tanlang:",
                                    reply_markup=InlineKeyboardMarkup(kb))

# ═══════════════════════════════════════════════════════
#  INLINE TUGMALAR HANDLERI
# ═══════════════════════════════════════════════════════
async def on_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q   = update.callback_query
    await q.answer()
    uid = q.from_user.id
    kind, _, val = q.data.partition(":")

    # --- Kategoriya tanlash ---
    if kind == "c":
        pend = context.user_data.pop("pending", None)
        if not pend:
            return await q.edit_message_text("Muddati o'tgan. Qaytadan kiriting.")
        typ, amount, note = pend
        cat = (INC_CATS if typ=="income" else EXP_CATS)[int(val)]
        await q.edit_message_text("✅ Saqlandi")
        await save(update, context, uid, typ, amount, cat, note, q.message.reply_text)

    # --- Bekor qilish ---
    elif kind == "x":
        context.user_data.pop("pending", None)
        await q.edit_message_text("Bekor qilindi.")

    # --- Hisobot davri ---
    elif kind == "p":
        await q.edit_message_text(build_report(uid, val), parse_mode="Markdown")

    # --- O'chirish ---
    elif kind == "d":
        db("DELETE FROM tx WHERE id=? AND uid=?", (int(val), uid))
        await q.edit_message_text("🗑 O'chirildi. Yangilangan ro'yxat uchun «📜 Tarix» ni bosing.")

    # --- 📈 Tahlil ---
    elif kind == "an":
        if val == "chart":
            await q.edit_message_text(build_chart(uid), parse_mode="Markdown")
        elif val == "saving":
            await q.edit_message_text(build_savings_rating(uid), parse_mode="Markdown")
        elif val == "advice":
            await q.edit_message_text(build_advice(uid), parse_mode="Markdown")
        elif val == "compare":
            await q.edit_message_text(build_month_compare(uid), parse_mode="Markdown")
        elif val == "family":
            uids = get_family_uids(uid)
            if len(uids) <= 1:
                await q.edit_message_text("👨‍👩‍👧 Oilaviy hisobot uchun avval guruh yarating.\n⚙️ → Oilaviy hisob")
            else:
                await q.edit_message_text(build_report(uid,"month",group=True), parse_mode="Markdown")

    # --- ⚙️ Sozlamalar ---
    elif kind == "set":
        if val == "monthlim":
            row = db("SELECT amount FROM lim WHERE uid=?", (uid,), "one")
            cur = f"Hozirgi limit: {fmt(row[0])}\n\n" if row and row[0] else ""
            context.user_data["mode"] = "limit"
            await q.edit_message_text(cur + "Oylik xarajat limitini yozing (masalan: 3mln).\nO'chirish uchun: 0")
        elif val == "catlim":
            kb  = [[InlineKeyboardButton(EXP_CATS[i], callback_data=f"cl:{i}")]
                   for i in range(len(EXP_CATS))]
            kb.append([InlineKeyboardButton("❌ Yopish", callback_data="x")])
            await q.edit_message_text("📂 Qaysi kategoriya uchun limit?",
                                      reply_markup=InlineKeyboardMarkup(kb))
        elif val == "recur":
            await show_recurring(q, uid)
        elif val == "remind":
            context.user_data["mode"] = "reminder"
            await show_reminder(q, uid)
        elif val == "currency":
            context.user_data["mode"] = "currency"
            await show_currency(q)
        elif val == "family":
            await show_family(q, uid)

    # --- 📂 Kategoriya limit tanlash ---
    elif kind == "cl":
        cat = EXP_CATS[int(val)]
        row = db("SELECT amount FROM cat_limits WHERE uid=? AND cat=?", (uid,cat), "one")
        cur = f"Hozirgi: {fmt(row[0])}\n\n" if row else ""
        context.user_data["mode"]         = "cat_limit_val"
        context.user_data["cat_limit_cat"] = cat
        await q.edit_message_text(f"{cur}*{cat}* uchun oylik limit yozing:\n"
                                  f"masalan: 500000 yoki 1mln\nO'chirish: 0",
                                  parse_mode="Markdown")

    # --- 🔁 Takroriy: tur tanlash ---
    elif kind == "ra":
        context.user_data["mode"]          = "recurring_add"
        context.user_data["recurring_type"] = val
        label = "Daromad" if val=="income" else "Xarajat"
        await q.edit_message_text(f"{label} summasini yozing:\nmasalan: 500000 oylik")

    # --- 🔁 Takroriy: interval tanlash ---
    elif kind == "ri":
        pend = context.user_data.pop("recurring_pending", None)
        if pend:
            typ, amt, cat, note = pend
            ivl  = int(val)
            nd   = (now()+timedelta(days=ivl)).strftime("%Y-%m-%d %H:%M:%S")
            db("INSERT INTO recurring(uid,type,amount,cat,note,interval_days,next_date) VALUES(?,?,?,?,?,?,?)",
               (uid,typ,amt,cat,note,ivl,nd))
            imap = {1:"Kunlik",7:"Haftalik",30:"Oylik"}
            await q.edit_message_text(f"✅ Takroriy to'lov qo'shildi!\n"
                                      f"{'➕' if typ=='income' else '➖'} {fmt(amt)} — {cat} ({imap[ivl]})")
        else:
            await q.edit_message_text("❌ Muammo yuz berdi.")

    # --- 🔁 Takroriy: o'chirish ---
    elif kind == "rd":
        db("DELETE FROM recurring WHERE id=? AND uid=?", (int(val), uid))
        await q.edit_message_text("🗑 Takroriy to'lov o'chirildi.")

    # --- 🔔 Eslatma ---
    elif kind == "rem":
        if val == "weekly":
            row = db("SELECT weekly FROM reminders WHERE uid=?", (uid,), "one")
            if row:
                nw = 0 if row[0] else 1
                db("UPDATE reminders SET weekly=? WHERE uid=?", (nw, uid))
                st = "yoqildi ✅" if nw else "o'chirildi ❌"
                await q.edit_message_text(f"Haftalik hisobot {st}.")
        elif val == "off":
            db("DELETE FROM reminders WHERE uid=?", (uid,))
            await q.edit_message_text("🔕 Eslatma o'chirildi.")

    # --- 👨‍👩‍👧 Oila ---
    elif kind == "fam":
        if val == "create":
            if get_family_group(uid):
                return await q.edit_message_text("❌ Siz allaqachon guruhdasiz.")
            code = gen_code()
            db("INSERT INTO family(admin_uid,code) VALUES(?,?)", (uid,code))
            gid  = db("SELECT last_insert_rowid()", fetch="one")[0]
            db("INSERT INTO family_members(group_id,uid) VALUES(?,?)", (gid,uid))
            await q.edit_message_text(
                f"✅ Oilaviy guruh yaratildi!\n\nGuruh kodi: `{code}`\n\n"
                f"Kodni oila a'zolaringizga yuboring.\nUlar ⚙️ → Oilaviy hisob → Guruhga qo'shilish orqali ulanadi.",
                parse_mode="Markdown")
        elif val == "join":
            context.user_data["mode"] = "family_join"
            await q.edit_message_text("🔗 6 ta belgidan iborat guruh kodini kiriting:")
        elif val == "leave":
            gid = get_family_group(uid)
            if gid:
                db("DELETE FROM family_members WHERE group_id=? AND uid=?", (gid,uid))
                cnt = db("SELECT COUNT(*) FROM family_members WHERE group_id=?", (gid,), "one")[0]
                if cnt == 0: db("DELETE FROM family WHERE group_id=?", (gid,))
                await q.edit_message_text("👋 Guruhdan chiqdingiz.")
            else:
                await q.edit_message_text("❌ Siz hech qanday guruhda emassiz.")

# ═══════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════
def start_dummy_server():
    """Render Web Service uchun portni ochiq saqlash"""
    from http.server import HTTPServer, BaseHTTPRequestHandler
    import threading
    class SimpleHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Bot is alive!")
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), SimpleHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

def main():
    if not TOKEN:
        raise SystemExit("BOT_TOKEN o'rnatilmagan!")
    start_dummy_server()
    init_db()
    
    # Ulanish kutish vaqtini (timeout) oshiramiz
    from telegram.request import HTTPXRequest
    req = HTTPXRequest(connect_timeout=30.0, read_timeout=30.0)
    app = Application.builder().token(TOKEN).request(req).build()

    # Handlerlar
    app.add_handler(CommandHandler(["start","help"], start))
    app.add_handler(CommandHandler("balans",  balance))
    app.add_handler(CommandHandler("tarix",   history))
    app.add_handler(CommandHandler("hisobot", report_menu))
    app.add_handler(CallbackQueryHandler(on_cb))
    app.add_handler(MessageHandler(filters.VOICE, on_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    # Avtomatik vazifalar (JobQueue)
    jq = app.job_queue
    jq.run_repeating(job_daily_reminder, interval=60,   first=5)   # Har daqiqa
    jq.run_repeating(job_weekly_report,  interval=3600, first=30)  # Har soat
    jq.run_repeating(job_recurring,      interval=3600, first=60)  # Har soat

    print("Bot ishga tushdi ✅  v2.0 — Kengaytirilgan")
    print("Funksiyalar: Hisobot | Tahlil | Grafik | Eslatma | Valyuta | Oila | Takroriy to'lovlar")
    app.run_polling()


if __name__ == "__main__":
    import asyncio
    asyncio.set_event_loop(asyncio.new_event_loop())
    main()
