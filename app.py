import os
import sqlite3
from functools import wraps

from flask import Flask, g, jsonify, redirect, request, send_from_directory, session
from werkzeug.security import check_password_hash, generate_password_hash

BASE = os.path.dirname(os.path.abspath(__file__))
PUBLIC = os.path.join(BASE, "public")
DB = os.path.join(BASE, "estoque.db")

app = Flask(__name__, static_folder=None)
app.secret_key = os.environ.get("SECRET_KEY", "troque-esta-chave")


# ---------- banco ----------
def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def fechar(_):
    conn = g.pop("db", None)
    if conn:
        conn.close()


def init_db():
    conn = sqlite3.connect(DB)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS usuarios(
            id INTEGER PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            senha_hash TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS produtos(
            id INTEGER PRIMARY KEY,
            usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
            codigo TEXT NOT NULL,
            nome TEXT NOT NULL,
            categoria TEXT NOT NULL,
            custo REAL NOT NULL DEFAULT 0,
            venda REAL NOT NULL DEFAULT 0,
            qtd INTEGER NOT NULL DEFAULT 0,
            minimo INTEGER NOT NULL DEFAULT 0,
            UNIQUE(usuario_id, codigo));
        CREATE TABLE IF NOT EXISTS movimentacoes(
            id INTEGER PRIMARY KEY,
            usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
            produto_id INTEGER,
            produto_nome TEXT NOT NULL,
            tipo TEXT NOT NULL,
            quantidade INTEGER NOT NULL,
            entregue_por TEXT,
            recebido_por TEXT,
            preco_venda REAL,
            preco_custo REAL,
            criado_em TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')));
        """
    )
    cols = [r[1] for r in conn.execute("PRAGMA table_info(movimentacoes)")]
    for c in ("entregue_por", "recebido_por"):
        if c not in cols:
            conn.execute(f"ALTER TABLE movimentacoes ADD COLUMN {c} TEXT")
    if "preco_venda" not in cols:
        conn.execute("ALTER TABLE movimentacoes ADD COLUMN preco_venda REAL")
        conn.execute("ALTER TABLE movimentacoes ADD COLUMN preco_custo REAL")
        conn.execute("UPDATE movimentacoes SET preco_venda=(SELECT venda FROM produtos p WHERE p.id=movimentacoes.produto_id), preco_custo=(SELECT custo FROM produtos p WHERE p.id=movimentacoes.produto_id)")
    if not conn.execute("SELECT 1 FROM usuarios").fetchone():
        conn.execute(
            "INSERT INTO usuarios(email, senha_hash) VALUES(?,?)",
            ("admin@loja.com", generate_password_hash("123456")),
        )
    conn.commit()
    conn.close()


def logado(f):
    @wraps(f)
    def wrap(*a, **k):
        if "uid" not in session:
            return jsonify(erro="Faça login para continuar."), 401
        return f(*a, **k)
    return wrap


# ---------- páginas ----------
@app.get("/")
@app.get("/index.html")
def home():
    return send_from_directory(PUBLIC, "index.html")


@app.get("/login.html")
def pagina_login():
    return send_from_directory(PUBLIC, "login.html")


@app.get("/recursos.html")
def pagina_recursos():
    return send_from_directory(PUBLIC, "recursos.html")


@app.get("/movimentacoes.html")
def pagina_movimentacoes():
    if "uid" not in session:
        return redirect("/login.html")
    return send_from_directory(PUBLIC, "movimentacoes.html")


@app.get("/relatorios.html")
def pagina_relatorios():
    if "uid" not in session:
        return redirect("/login.html")
    return send_from_directory(PUBLIC, "relatorios.html")


@app.get("/cadastro.html")
def pagina_cadastro():
    return send_from_directory(PUBLIC, "cadastro.html")


@app.get("/app.html")
def pagina_app():
    if "uid" not in session:
        return redirect("/login.html")
    return send_from_directory(PUBLIC, "app.html")


@app.get("/style.css")
def css():
    return send_from_directory(PUBLIC, "style.css")


@app.get("/logout")
def logout():
    session.clear()
    return redirect("/login.html")


# ---------- api: login ----------
@app.post("/api/login")
def api_login():
    d = request.get_json(silent=True) or {}
    u = db().execute("SELECT * FROM usuarios WHERE email=?", (d.get("email", "").strip().lower(),)).fetchone()
    if not u or not check_password_hash(u["senha_hash"], d.get("senha", "")):
        return jsonify(erro="E-mail ou senha incorretos."), 401
    session["uid"] = u["id"]
    return jsonify(ok=True)


@app.post("/api/registrar")
def api_registrar():
    d = request.get_json(silent=True) or {}
    email = str(d.get("email", "")).strip().lower()
    senha = str(d.get("senha", ""))
    if "@" not in email or "." not in email.split("@")[-1]:
        return jsonify(erro="Digite um e-mail válido."), 400
    if len(senha) < 6:
        return jsonify(erro="A senha precisa ter pelo menos 6 caracteres."), 400
    try:
        cur = db().execute("INSERT INTO usuarios(email, senha_hash) VALUES(?,?)", (email, generate_password_hash(senha)))
        db().commit()
    except sqlite3.IntegrityError:
        return jsonify(erro="Esse e-mail já tem conta. Tente entrar."), 409
    session["uid"] = cur.lastrowid
    return jsonify(ok=True), 201


# ---------- api: produtos ----------
def ler_produto(d):
    try:
        p = dict(
            codigo=str(d.get("codigo", "")).strip(),
            nome=str(d.get("nome", "")).strip(),
            categoria=str(d.get("categoria", "")).strip(),
            custo=float(d.get("custo") or 0),
            venda=float(d.get("venda") or 0),
            qtd=int(d.get("qtd") or 0),
            minimo=int(d.get("minimo") or 0),
        )
    except (TypeError, ValueError):
        return None, "Confira os números: preço e quantidade precisam ser válidos."
    if not p["nome"] or not p["codigo"]:
        return None, "Nome e código são obrigatórios."
    if min(p["custo"], p["venda"], p["qtd"], p["minimo"]) < 0:
        return None, "Valores não podem ser negativos."
    return p, None


@app.get("/api/produtos")
@logado
def listar():
    rows = db().execute("SELECT * FROM produtos WHERE usuario_id=? ORDER BY id DESC", (session["uid"],)).fetchall()
    return jsonify([dict(r) for r in rows])


@app.post("/api/produtos")
@logado
def criar():
    p, erro = ler_produto(request.get_json(silent=True) or {})
    if erro:
        return jsonify(erro=erro), 400
    p["uid"] = session["uid"]
    try:
        cur = db().execute(
            "INSERT INTO produtos(usuario_id,codigo,nome,categoria,custo,venda,qtd,minimo) VALUES(:uid,:codigo,:nome,:categoria,:custo,:venda,:qtd,:minimo)", p)
        db().commit()
    except sqlite3.IntegrityError:
        return jsonify(erro="Já existe um produto com esse código."), 409
    return jsonify(id=cur.lastrowid), 201


@app.put("/api/produtos/<int:pid>")
@logado
def atualizar(pid):
    p, erro = ler_produto(request.get_json(silent=True) or {})
    if erro:
        return jsonify(erro=erro), 400
    try:
        cur = db().execute(
            "UPDATE produtos SET codigo=:codigo,nome=:nome,categoria=:categoria,custo=:custo,venda=:venda,minimo=:minimo WHERE id=:id AND usuario_id=:uid",
            {**p, "id": pid, "uid": session["uid"]})
        db().commit()
    except sqlite3.IntegrityError:
        return jsonify(erro="Já existe um produto com esse código."), 409
    if cur.rowcount == 0:
        return jsonify(erro="Produto não encontrado."), 404
    return jsonify(ok=True)


@app.delete("/api/produtos/<int:pid>")
@logado
def excluir(pid):
    db().execute("DELETE FROM produtos WHERE id=? AND usuario_id=?", (pid, session["uid"]))
    db().commit()
    return jsonify(ok=True)


@app.post("/api/produtos/<int:pid>/venda")
@logado
def vender(pid):
    d = request.get_json(silent=True) or {}
    entregue = str(d.get("entregue_por", "")).strip()[:80]
    recebido = str(d.get("recebido_por", "")).strip()[:80]
    if not entregue or not recebido:
        return jsonify(erro="Informe quem entregou e quem recebeu."), 400
    uid, cx = session["uid"], db()
    p = cx.execute("SELECT nome, venda, custo FROM produtos WHERE id=? AND usuario_id=?", (pid, uid)).fetchone()
    if not p:
        return jsonify(erro="Produto não encontrado."), 404
    cur = cx.execute("UPDATE produtos SET qtd=qtd-1 WHERE id=? AND usuario_id=? AND qtd>=1", (pid, uid))
    if cur.rowcount == 0:
        return jsonify(erro="Esse produto está sem estoque."), 409
    cx.execute(
        "INSERT INTO movimentacoes(usuario_id,produto_id,produto_nome,tipo,quantidade,entregue_por,recebido_por,preco_venda,preco_custo) VALUES(?,?,?,'venda',1,?,?,?,?)",
        (uid, pid, p["nome"], entregue, recebido, p["venda"], p["custo"]))
    cx.commit()
    return jsonify(ok=True)


@app.post("/api/produtos/<int:pid>/entrada")
@logado
def entrada(pid):
    d = request.get_json(silent=True) or {}
    try:
        qtd = int(d.get("quantidade"))
    except (TypeError, ValueError):
        qtd = 0
    if not 1 <= qtd <= 100000:
        return jsonify(erro="Informe uma quantidade de 1 a 100000."), 400
    entregue = str(d.get("entregue_por", "")).strip()[:80]
    recebido = str(d.get("recebido_por", "")).strip()[:80]
    if not entregue or not recebido:
        return jsonify(erro="Informe quem entregou e quem recebeu."), 400
    uid, cx = session["uid"], db()
    p = cx.execute("SELECT nome, venda, custo FROM produtos WHERE id=? AND usuario_id=?", (pid, uid)).fetchone()
    if not p:
        return jsonify(erro="Produto não encontrado."), 404
    cx.execute("UPDATE produtos SET qtd=qtd+? WHERE id=? AND usuario_id=?", (qtd, pid, uid))
    cx.execute(
        "INSERT INTO movimentacoes(usuario_id,produto_id,produto_nome,tipo,quantidade,entregue_por,recebido_por,preco_venda,preco_custo) VALUES(?,?,?,'entrada',?,?,?,?,?)",
        (uid, pid, p["nome"], qtd, entregue, recebido, p["venda"], p["custo"]))
    cx.commit()
    return jsonify(ok=True)


# ---------- api: movimentações ----------
@app.get("/api/movimentacoes")
@logado
def movimentacoes():
    rows = db().execute(
        "SELECT * FROM movimentacoes WHERE usuario_id=? ORDER BY id DESC LIMIT 200", (session["uid"],)).fetchall()
    return jsonify([dict(r) for r in rows])


@app.get("/api/relatorio")
@logado
def relatorio():
    rows = db().execute(
        "SELECT tipo, quantidade, preco_venda, preco_custo, criado_em FROM movimentacoes "
        "WHERE usuario_id=? AND criado_em >= strftime('%Y-%m-%dT%H:%M:%SZ','now','-91 days') "
        "ORDER BY criado_em LIMIT 20000", (session["uid"],)).fetchall()
    return jsonify([dict(r) for r in rows])


init_db()

if __name__ == "__main__":
    app.run(debug=True)
