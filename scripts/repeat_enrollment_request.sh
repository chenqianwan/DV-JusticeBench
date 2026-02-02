#!/bin/bash
# 重复发送课程注册请求，间隔 0.1 秒

# 设置颜色
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# 计数器
count=0
success_count=0
fail_count=0

echo -e "${GREEN}开始重复发送请求，间隔 0.1 秒${NC}"
echo -e "${YELLOW}按 Ctrl+C 停止${NC}"
echo "----------------------------------------"

while true; do
    count=$((count + 1))
    timestamp=$(date '+%Y-%m-%d %H:%M:%S.%3N')
    
    echo -e "\n${YELLOW}[请求 #${count}] ${timestamp}${NC}"
    
    # 执行 curl 请求
    response=$(curl -s -w "\nHTTP_CODE:%{http_code}\nTIME:%{time_total}" \
        'https://sisprod.psft.ust.hk/psc/SISPROD/EMPLOYEE/HRMS/c/SA_LEARNER_SERVICES_2.SSR_SSENRL_CART.GBL' \
        -H 'Accept: */*' \
        -H 'Accept-Language: en-GB,en-US;q=0.9,en;q=0.8,zh-CN;q=0.7,zh-TW;q=0.6,zh;q=0.5' \
        -H 'Connection: keep-alive' \
        -H 'Content-Type: application/x-www-form-urlencoded' \
        -b '_ga_3E3P4SB1VP=GS2.1.s1767757937$o2$g0$t1767757937$j60$l0$h0; PS_DEVICEFEATURES=width:1728 height:1117 pixelratio:2 touch:0 geolocation:1 websockets:1 webworkers:1 datepicker:1 dtpicker:1 timepicker:1 dnd:1 sessionstorage:1 localstorage:1 history:1 canvas:1 svg:1 postmessage:1 hc:0 maf:0; PS_TokenSite=https://sisprod.psft.ust.hk/psp/SISPROD/?JSESSIONID; SignOnDefault=; HPTabName=DEFAULT; HPTabNameRemote=; LastActiveTab=DEFAULT; JSESSIONID=41L-y_Q5F25FVISRAAIuOZDNUvtS3YZw!379933844; PS_LASTSITE=https://sisprod.psft.ust.hk/psp/SISPROD/; ExpirePage=https://sisprod.psft.ust.hk/psp/SISPROD/; PS_LOGINLIST=https://sisprod.psft.ust.hk/SISPROD; PS_TOKEN=qQAAAAQDAgEBAAAAvAIAAAAAAAAsAAAABABTaGRyAk4AcQg4AC4AMQAwABQtdCic6yfiDQm94/YdknJ1oOxt4WkAAAAFAFNkYXRhXXicLYnLCkBQAESPR5byI25c8vgAZCPF3kLKQsrKz/k4k0zNnJnmAnzPdRzxcfkUHqzsbJzyTdAw0BGNTLTMLGIv55YES0EspkpL9XdDpmXUSmVOrV3qTeEF/YEM8g==; ps_theme=node:HRMS portal:EMPLOYEE theme_id:Z_THEME_CLASSIC accessibility:N formfactor:3 piamode:2; https%3a%2f%2fsisprod.psft.ust.hk%2fpsp%2fsisprod%2femployee%2fhrms%2frefresh=list:%20%3ftab%3dremoteunifieddashboard%7c%3frp%3dremoteunifieddashboard|; psback=%22%22url%22%3A%22https%3A%2F%2Fsisprod.psft.ust.hk%2Fpsp%2FSISPROD%2FEMPLOYEE%2FHRMS%2Fc%2FSA_LEARNER_SERVICES.SSS_STUDENT_CENTER.GBL%3Fpslnkid%3DZ_HC_SSS_STUDENT_CENTER_LNK%26FolderPath%3DPORTAL_ROOT_OBJECT.Z_HC_SSS_STUDENT_CENTER_LNK%26IsFolder%3Dfalse%26IgnoreParamTempl%3DFolderPath%252cIsFolder%22%20%22label%22%3A%22Enrollment%20Shopping%20Cart%22%20%22origin%22%3A%22PIA%22%20%22layout%22%3A%220%22%20%22refurl%22%3A%22https%3A%2F%2Fsisprod.psft.ust.hk%2Fpsc%2FSISPROD%2FEMPLOYEE%2FHRMS%22%22; PS_TOKENEXPIRE=27_Jan_2026_09:39:54_GMT' \
        -H 'Origin: https://sisprod.psft.ust.hk' \
        -H 'Referer: https://sisprod.psft.ust.hk/psc/SISPROD/EMPLOYEE/HRMS/c/SA_LEARNER_SERVICES_2.SSR_SSENRL_CART.GBL?Page=SSR_SSENRL_CART&Action=A&ExactKeys=Y' \
        -H 'Sec-Fetch-Dest: empty' \
        -H 'Sec-Fetch-Mode: cors' \
        -H 'Sec-Fetch-Site: same-origin' \
        -H 'User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36' \
        -H 'sec-ch-ua: "Google Chrome";v="143", "Chromium";v="143", "Not A(Brand";v="24"' \
        -H 'sec-ch-ua-mobile: ?0' \
        -H 'sec-ch-ua-platform: "macOS"' \
        --data-raw 'ICAJAX=1&ICNAVTYPEDROPDOWN=1&ICType=Panel&ICElementNum=0&ICStateNum=4&ICAction=DERIVED_REGFRM1_LINK_ADD_ENRL&ICModelCancel=0&ICXPos=0&ICYPos=109&ResponsetoDiffFrame=-1&TargetFrameName=None&FacetPath=None&ICFocus=&ICSaveWarningFilter=0&ICChanged=-1&ICSkipPending=0&ICAutoSave=0&ICResubmit=0&ICSID=9fjdlBsAXlqL1FKBOYWAGELNDb%2FEPSuEwkelQ8Z8AIs%3D&ICActionPrompt=false&ICTypeAheadID=&ICBcDomData=C~HC_SSR_SSENRL_CART_GBL2~EMPLOYEE~HRMS~SA_LEARNER_SERVICES_2.SSR_SSENRL_CART.GBL~UnknownValue~Enrollment%20Shopping%20Cart~UnknownValue~UnknownValue~https%3A%2F%2Fsisprod.psft.ust.hk%2Fpsp%2FSISPROD%2FEMPLOYEE%2FHRMS%2Fc%2FSA_LEARNER_SERVICES_2.SSR_SSENRL_CART.GBL~UnknownValue*C~Z_HC_SSS_STUDENT_CENTER_LNK~EMPLOYEE~HRMS~SA_LEARNER_SERVICES.SSS_STUDENT_CENTER.GBL~UnknownValue~Student%20Center~UnknownValue~UnknownValue~https%3A%2F%2Fsisprod.psft.ust.hk%2Fpsp%2FSISPROD%2FEMPLOYEE%2FHRMS%2Fc%2FSA_LEARNER_SERVICES.SSS_STUDENT_CENTER.GBL~UnknownValue&ICPanelName=&ICFind=&ICAddCount=&ICAppClsData=&DERIVED_SSTSNAV_SSTS_MAIN_GOTO$7$=9999&DERIVED_REGFRM1_CLASS_NBR=&DERIVED_REGFRM1_SSR_CLS_SRCH_TYPE$249$=06&P_SELECT$chk$0=Y&P_SELECT$0=Y&P_SELECT$chk$1=Y&P_SELECT$1=Y&P_SELECT$chk$2=Y&P_SELECT$2=Y&P_SELECT$chk$3=Y&P_SELECT$3=Y&P_SELECT$chk$4=Y&P_SELECT$4=Y&P_SELECT$chk$5=Y&P_SELECT$5=Y&DERIVED_SSTSNAV_SSTS_MAIN_GOTO$8$=9999' \
        --max-time 10 \
        2>&1)
    
    # 提取状态码和响应时间
    http_code=$(echo "$response" | grep "HTTP_CODE:" | cut -d':' -f2)
    time_total=$(echo "$response" | grep "TIME:" | cut -d':' -f2)
    
    # 判断成功或失败
    if [ "$http_code" = "200" ]; then
        success_count=$((success_count + 1))
        echo -e "${GREEN}✓ 成功${NC} - 状态码: ${http_code} - 响应时间: ${time_total}s"
    else
        fail_count=$((fail_count + 1))
        echo -e "${RED}✗ 失败${NC} - 状态码: ${http_code}"
    fi
    
    # 显示统计信息
    echo -e "统计: 总计 ${count} | 成功 ${GREEN}${success_count}${NC} | 失败 ${RED}${fail_count}${NC}"
    
    # 等待 0.1 秒
    sleep 0.1
done
