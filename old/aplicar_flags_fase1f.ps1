$path = '.\app\api\provisioning.py'
$backup = '.\app\api\provisioning.py.bak_phase1f'

if (-not (Test-Path $path)) { throw "Arquivo não encontrado: $path" }

Copy-Item $path $backup -Force
$text = Get-Content $path -Raw -Encoding UTF8

$needle1 = @'
        try:
            _delete_target_user(
                client,
                str(result.guid),
                target_ip,
            )
'@

$replacement1 = @'
        try:
            # FALHA CONTROLADA - somente para Fase 1F.
            # O valor precisa ser exatamente "1".
            import os
            if os.getenv("AVAYA_PROVISIONING_TEST_FORCE_COMPENSATION_FAIL") == "1":
                logger.warning(
                    "[PROVISIONING TEST] Falha de compensação simulada | "
                    "target_ip=%s | guid=%s",
                    target_ip,
                    result.guid,
                )
                raise RuntimeError(
                    "TEST_FORCE_COMPENSATION_FAIL: falha controlada da compensação."
                )

            _delete_target_user(
                client,
                str(result.guid),
                target_ip,
            )
'@

$needle2 = @'
    try:
        _cache_verified_user(
            db,
            verified_user,
            target_ip,
        )
'@

$replacement2 = @'
    try:
        # FALHA CONTROLADA - somente para Fase 1F.
        # O usuário já foi validado no Avaya; não deve ser apagado.
        import os
        if os.getenv("AVAYA_PROVISIONING_TEST_FORCE_CACHE_FAIL") == "1":
            logger.warning(
                "[PROVISIONING TEST] Falha de cache simulada | "
                "target_ip=%s | guid=%s",
                target_ip,
                result.guid,
            )
            raise RuntimeError(
                "TEST_FORCE_CACHE_FAIL: falha controlada da persistência do cache."
            )

        _cache_verified_user(
            db,
            verified_user,
            target_ip,
        )
'@

if (($text.Split($needle1).Count - 1) -ne 1) {
    throw "Bloco de compensação não encontrado exatamente uma vez. Nenhuma alteração foi aplicada."
}
if (($text.Split($needle2).Count - 1) -ne 1) {
    throw "Bloco de cache não encontrado exatamente uma vez. Nenhuma alteração foi aplicada."
}

$text = $text.Replace($needle1, $replacement1)
$text = $text.Replace($needle2, $replacement2)

Set-Content $path $text -Encoding UTF8

python -m py_compile $path
if ($LASTEXITCODE -ne 0) {
    Copy-Item $backup $path -Force
    throw "py_compile falhou. Arquivo restaurado do backup."
}

Write-Host "Fase 1F: flags adicionadas com sucesso." -ForegroundColor Green
Write-Host "Backup: $backup"
Write-Host "Desabilitadas por padrão; só atuam quando o valor da variável é exatamente 1."
