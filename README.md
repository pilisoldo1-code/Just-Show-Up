# POC: Supabase + Python + GitHub + Vercel

Prueba de concepto que integra:
- **Supabase**: base de datos Postgres.
- **Python (FastAPI)**: backend y frontend (HTML server-side con Jinja2).
- **GitHub**: control de versiones y origen del deploy.
- **Vercel**: hosting, desplegando automáticamente desde GitHub.

## 1. Supabase

```bash
supabase login
supabase link --project-ref TU_PROJECT_REF
supabase db push
```

- `supabase login` abre el navegador para autenticarte (una sola vez).
- `TU_PROJECT_REF` es el ID del proyecto (Project Settings > General en el dashboard de Supabase).
- `supabase db push` aplica la migración en `supabase/migrations/` que crea la tabla `items`.

Después copiá `.env.example` a `.env` y completá con los datos de tu proyecto
(Project Settings > API en el dashboard):

```bash
cp .env.example .env
```

## 2. Desarrollo local

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Abrí http://localhost:8000

## 3. GitHub

```bash
gh auth login
gh repo create NOMBRE-DEL-REPO --private --source=. --remote=origin --push
```

`gh auth login` te pide autenticarte por navegador. El segundo comando crea el
repo en tu cuenta y sube el código.

## 4. Vercel

```bash
vercel login
vercel link
vercel env add SUPABASE_URL
vercel env add SUPABASE_KEY
vercel --prod
```

- `vercel login` abre el navegador para autenticarte.
- `vercel link` conecta esta carpeta a un proyecto de Vercel (nuevo o existente).
- Las variables de entorno se cargan una vez (te pide el valor por consola).
- Para que cada push a GitHub dispare un deploy automático, importá el repo
  desde el dashboard de Vercel (New Project > Import Git Repository) después
  de haberlo subido a GitHub.

## Notas de seguridad

- Las políticas de Row Level Security en `supabase/migrations/` permiten
  lectura/escritura anónima sobre `items` — sirve para la POC, **no uses esto
  en producción** con datos reales sin revisar las políticas.
- El archivo `.env` nunca se sube a git (está en `.gitignore`).
