$session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
$session.UserAgent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
$session.Cookies.Add((New-Object System.Net.Cookie("_ga_3E3P4SB1VP", "GS2.1.s1767757937`$o2`$g0`$t1767757937`$j60`$l0`$h0", "/", ".ust.hk")))
$session.Cookies.Add((New-Object System.Net.Cookie("PS_DEVICEFEATURES", "width:1728 height:1117 pixelratio:2 touch:0 geolocation:1 websockets:1 webworkers:1 datepicker:1 dtpicker:1 timepicker:1 dnd:1 sessionstorage:1 localstorage:1 history:1 canvas:1 svg:1 postmessage:1 hc:0 maf:0", "/", ".sisprod.psft.ust.hk")))
$session.Cookies.Add((New-Object System.Net.Cookie("PS_TokenSite", "https://sisprod.psft.ust.hk/psp/SISPROD/?JSESSIONID", "/", "sisprod.psft.ust.hk")))
$session.Cookies.Add((New-Object System.Net.Cookie("SignOnDefault", "", "/", "sisprod.psft.ust.hk")))
$session.Cookies.Add((New-Object System.Net.Cookie("HPTabName", "DEFAULT", "/", "sisprod.psft.ust.hk")))
$session.Cookies.Add((New-Object System.Net.Cookie("HPTabNameRemote", "", "/", "sisprod.psft.ust.hk")))
$session.Cookies.Add((New-Object System.Net.Cookie("LastActiveTab", "DEFAULT", "/", "sisprod.psft.ust.hk")))
$session.Cookies.Add((New-Object System.Net.Cookie("JSESSIONID", "41L-y_Q5F25FVISRAAIuOZDNUvtS3YZw!379933844", "/", "sisprod.psft.ust.hk")))
$session.Cookies.Add((New-Object System.Net.Cookie("PS_LASTSITE", "https://sisprod.psft.ust.hk/psp/SISPROD/", "/", "sisprod.psft.ust.hk")))
$session.Cookies.Add((New-Object System.Net.Cookie("ExpirePage", "https://sisprod.psft.ust.hk/psp/SISPROD/", "/", "sisprod.psft.ust.hk")))
$session.Cookies.Add((New-Object System.Net.Cookie("PS_LOGINLIST", "https://sisprod.psft.ust.hk/SISPROD", "/", "sisprod.psft.ust.hk")))
$session.Cookies.Add((New-Object System.Net.Cookie("PS_TOKEN", "qQAAAAQDAgEBAAAAvAIAAAAAAAAsAAAABABTaGRyAk4AcQg4AC4AMQAwABQtdCic6yfiDQm94/YdknJ1oOxt4WkAAAAFAFNkYXRhXXicLYnLCkBQAESPR5byI25c8vgAZCPF3kLKQsrKz/k4k0zNnJnmAnzPdRzxcfkUHqzsbJzyTdAw0BGNTLTMLGIv55YES0EspkpL9XdDpmXUSmVOrV3qTeEF/YEM8g==", "/", "sisprod.psft.ust.hk")))
$session.Cookies.Add((New-Object System.Net.Cookie("ps_theme", "node:HRMS portal:EMPLOYEE theme_id:Z_THEME_CLASSIC accessibility:N formfactor:3 piamode:2", "/", "sisprod.psft.ust.hk")))
$session.Cookies.Add((New-Object System.Net.Cookie("https%3a%2f%2fsisprod.psft.ust.hk%2fpsp%2fsisprod%2femployee%2fhrms%2frefresh", "list:%20%3ftab%3dremoteunifieddashboard%7c%3frp%3dremoteunifieddashboard|", "/", "sisprod.psft.ust.hk")))
$session.Cookies.Add((New-Object System.Net.Cookie("psback", "%22%22url%22%3A%22https%3A%2F%2Fsisprod.psft.ust.hk%2Fpsp%2FSISPROD%2FEMPLOYEE%2FHRMS%2Fc%2FSA_LEARNER_SERVICES.SSS_STUDENT_CENTER.GBL%3Fpslnkid%3DZ_HC_SSS_STUDENT_CENTER_LNK%26FolderPath%3DPORTAL_ROOT_OBJECT.Z_HC_SSS_STUDENT_CENTER_LNK%26IsFolder%3Dfalse%26IgnoreParamTempl%3DFolderPath%252cIsFolder%22%20%22label%22%3A%22Enrollment%20Shopping%20Cart%22%20%22origin%22%3A%22PIA%22%20%22layout%22%3A%220%22%20%22refurl%22%3A%22https%3A%2F%2Fsisprod.psft.ust.hk%2Fpsc%2FSISPROD%2FEMPLOYEE%2FHRMS%22%22", "/", "sisprod.psft.ust.hk")))
$session.Cookies.Add((New-Object System.Net.Cookie("PS_TOKENEXPIRE", "27_Jan_2026_09:39:54_GMT", "/", "sisprod.psft.ust.hk")))

$body = "ICAJAX=1&ICNAVTYPEDROPDOWN=1&ICType=Panel&ICElementNum=0&ICStateNum=4&ICAction=DERIVED_REGFRM1_LINK_ADD_ENRL&ICModelCancel=0&ICXPos=0&ICYPos=109&ResponsetoDiffFrame=-1&TargetFrameName=None&FacetPath=None&ICFocus=&ICSaveWarningFilter=0&ICChanged=-1&ICSkipPending=0&ICAutoSave=0&ICResubmit=0&ICSID=9fjdlBsAXlqL1FKBOYWAGELNDb%2FEPSuEwkelQ8Z8AIs%3D&ICActionPrompt=false&ICTypeAheadID=&ICBcDomData=C~HC_SSR_SSENRL_CART_GBL2~EMPLOYEE~HRMS~SA_LEARNER_SERVICES_2.SSR_SSENRL_CART.GBL~UnknownValue~Enrollment%20Shopping%20Cart~UnknownValue~UnknownValue~https%3A%2F%2Fsisprod.psft.ust.hk%2Fpsp%2FSISPROD%2FEMPLOYEE%2FHRMS%2Fc%2FSA_LEARNER_SERVICES_2.SSR_SSENRL_CART.GBL~UnknownValue*C~Z_HC_SSS_STUDENT_CENTER_LNK~EMPLOYEE~HRMS~SA_LEARNER_SERVICES.SSS_STUDENT_CENTER.GBL~UnknownValue~Student%20Center~UnknownValue~UnknownValue~https%3A%2F%2Fsisprod.psft.ust.hk%2Fpsp%2FSISPROD%2FEMPLOYEE%2FHRMS%2Fc%2FSA_LEARNER_SERVICES.SSS_STUDENT_CENTER.GBL~UnknownValue&ICPanelName=&ICFind=&ICAddCount=&ICAppClsData=&DERIVED_SSTSNAV_SSTS_MAIN_GOTO`$7`$=9999&DERIVED_REGFRM1_CLASS_NBR=&DERIVED_REGFRM1_SSR_CLS_SRCH_TYPE`$249`$=06&P_SELECT`$chk`$0=Y&P_SELECT`$0=Y&P_SELECT`$chk`$1=Y&P_SELECT`$1=Y&P_SELECT`$chk`$2=Y&P_SELECT`$2=Y&P_SELECT`$chk`$3=Y&P_SELECT`$3=Y&P_SELECT`$chk`$4=Y&P_SELECT`$4=Y&P_SELECT`$chk`$5=Y&P_SELECT`$5=Y&DERIVED_SSTSNAV_SSTS_MAIN_GOTO`$8`$=9999"

Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] 开始发送请求..."
Write-Host "URL: https://sisprod.psft.ust.hk/psc/SISPROD/EMPLOYEE/HRMS/c/SA_LEARNER_SERVICES_2.SSR_SSENRL_CART.GBL"
Write-Host "Action: DERIVED_REGFRM1_LINK_ADD_ENRL"
Write-Host ("-" * 80)

try {
    $response = Invoke-WebRequest -UseBasicParsing -Uri "https://sisprod.psft.ust.hk/psc/SISPROD/EMPLOYEE/HRMS/c/SA_LEARNER_SERVICES_2.SSR_SSENRL_CART.GBL" `
        -Method "POST" `
        -WebSession $session `
        -Headers @{
            "Accept"="*/*"
            "Accept-Encoding"="gzip, deflate, br, zstd"
            "Accept-Language"="en-GB,en-US;q=0.9,en;q=0.8,zh-CN;q=0.7,zh-TW;q=0.6,zh;q=0.5"
            "Origin"="https://sisprod.psft.ust.hk"
            "Referer"="https://sisprod.psft.ust.hk/psc/SISPROD/EMPLOYEE/HRMS/c/SA_LEARNER_SERVICES_2.SSR_SSENRL_CART.GBL?Page=SSR_SSENRL_CART&Action=A&ExactKeys=Y"
            "Sec-Fetch-Dest"="empty"
            "Sec-Fetch-Mode"="cors"
            "Sec-Fetch-Site"="same-origin"
            "sec-ch-ua"="`"Google Chrome`";v=`"143`", `"Chromium`";v=`"143`", `"Not A(Brand`";v=`"24`""
            "sec-ch-ua-mobile"="?0"
            "sec-ch-ua-platform"="`"macOS`""
        } `
        -ContentType "application/x-www-form-urlencoded" `
        -Body $body `
        -ErrorAction Stop

    Write-Host "状态码: $($response.StatusCode)"
    Write-Host "响应头 Content-Type: $($response.Headers.'Content-Type')"
    Write-Host "响应大小: $($response.Content.Length) bytes"
    Write-Host ("-" * 80)

    # 保存响应内容
    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $outputFile = "enrollment_response_$timestamp.html"
    $response.Content | Out-File -FilePath $outputFile -Encoding UTF8

    Write-Host "响应已保存到: $outputFile"

    # 显示响应内容预览
    if ($response.Content.Length -gt 0) {
        Write-Host "`n响应内容预览 (前500字符):"
        Write-Host $response.Content.Substring(0, [Math]::Min(500, $response.Content.Length))
    }

    # 显示新的 cookies
    if ($response.Headers.'Set-Cookie') {
        Write-Host "`n新的 Cookies:"
        $response.Headers.'Set-Cookie' | ForEach-Object { Write-Host "  $_" }
    }

} catch {
    Write-Host "错误: $($_.Exception.Message)"
    if ($_.Exception.Response) {
        Write-Host "状态码: $($_.Exception.Response.StatusCode.value__)"
    }
}
