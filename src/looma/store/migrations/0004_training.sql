-- Задания на дообучение: чем подняли, кому принадлежит, чем кончилось.
--
-- Отдельно от deployments: там — что развёрнуто и что вернуть после аренды,
-- здесь — работа с началом и концом, у которой есть результат (адаптер) и
-- которую никто не возвращает на место.
CREATE TABLE IF NOT EXISTS training_jobs (
    group_id    TEXT        PRIMARY KEY,
    label       TEXT        NOT NULL DEFAULT '',
    -- Тело запроса как пришло от клиента: модель, датасет (без самого
    -- файла), LoRA, расписание. Им же можно повторить обучение.
    request     JSONB       NOT NULL,
    account_id  BIGINT      REFERENCES accounts (id) ON DELETE SET NULL,
    -- running | done | failed | stopped
    state       TEXT        NOT NULL DEFAULT 'running',
    -- result.json головы, когда обучение кончилось.
    result      JSONB,
    error       TEXT        NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS training_jobs_account_idx ON training_jobs (account_id, created_at DESC);
