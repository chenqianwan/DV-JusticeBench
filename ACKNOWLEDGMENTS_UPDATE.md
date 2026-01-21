# Acknowledgments and Open Source Information Update

## Summary

Added comprehensive funding acknowledgments and open source information to both the README.md and web application interfaces (V1 and V2).

---

## 📝 Updates Made

### 1. README.md

#### Added New Section: "Acknowledgments"

**Data Sources:**
- China Judgments Online (https://wenshu.court.gov.cn/)
- Supreme People's Court 2025 Typical Anti-Domestic Violence Cases

**Funding Information:**
- HKUST Start-up Fund (R9911)
- Theme-based Research Scheme Grant (T45-205/21-N)
- InnoHK Initiative of the Innovation and Technology Commission of the Hong Kong Special Administrative Region Government
- Research Funding under HKUST-DXM AI for Finance Joint Laboratory (DXM25EG01)

**Open Source Statement:**
- Code: Available at [https://github.com/chenqianwan/DV-JusticeBench](https://github.com/chenqianwan/DV-JusticeBench)
- Data: 108 cases, 540 questions available for academic research
- License: MIT License - free for non-commercial research use

#### Updated GitHub Links

All GitHub repository references updated from:
- ❌ `https://github.com/chenqianwan/huangyidan1`

To:
- ✅ `https://github.com/chenqianwan/DV-JusticeBench`

Updated in sections:
- Contributing
- Contact
- Footer badge

---

### 2. Web Application V2 (`templates/index_v2.html`)

#### Footer Section Enhanced

**Added:**
- ⭐ GitHub Open Source Link
- Code & Dataset Available indicator
- Complete funding acknowledgment (English)

**Content:**
```
⭐ Open Source on GitHub | Code & Dataset Available

Funding: This work is funded in part by the HKUST Start-up Fund (R9911), 
Theme-based Research Scheme grant (T45-205/21-N), the InnoHK initiative 
of the Innovation and Technology Commission of the Hong Kong Special 
Administrative Region Government, and the research funding under HKUST-DXM 
AI for Finance Joint Laboratory (DXM25EG01).

© 2026 DV-JusticeBench Legal AI Research Platform
```

---

### 3. Web Application V1 (`templates/index.html`)

#### Footer Section Enhanced

**Added:**
- ⭐ GitHub Open Source Link (Chinese)
- Code & Dataset Available indicator (Chinese)
- Complete funding acknowledgment (Chinese)

**Content:**
```
⭐ 在 GitHub 上开源 | 代码与数据集已公开

基金资助：本研究得到 HKUST Start-up Fund (R9911)、Theme-based Research 
Scheme grant (T45-205/21-N)、香港特别行政区政府创新科技委员会 InnoHK 倡议、
以及 HKUST-DXM AI for Finance Joint Laboratory (DXM25EG01) 研究资助的
部分支持。

作者信息
[Huang Yidan & Chen Long contact cards]
```

---

## 🎯 Key Features

### Professional Presentation
- ✅ Prominent GitHub link with star icon
- ✅ Clear "Open Source" messaging
- ✅ Complete funding information
- ✅ Proper institutional acknowledgments

### Multi-Language Support
- ✅ English version in V2 web app
- ✅ Chinese version in V1 web app
- ✅ English version in README.md

### Visual Design
- ✅ Highlighted box in footer (light gray background)
- ✅ Blue hyperlinks (#2196F3)
- ✅ Proper font sizing and spacing
- ✅ Responsive layout

---

## 📊 Implementation Details

### Styling Approach

**V2 (English):**
- Inline styles for quick deployment
- Light background (#f5f5f5) for emphasis
- Font weight 500 for headings
- Smaller font (0.9em) for funding details

**V1 (Chinese):**
- Similar styling approach
- Positioned above author cards
- Gray color (#888, #666) for secondary text
- Rounded corners (8px border-radius)

### Link Behavior
- All GitHub links open in new tab (`target="_blank"`)
- Consistent blue color scheme
- No underline by default
- Hover effects inherited from main CSS

---

## 🌐 Access URLs

**GitHub Repository:**
- https://github.com/chenqianwan/DV-JusticeBench

**Web Applications:**
- V1 (Chinese): http://127.0.0.1:5001/
- V2 (English): http://127.0.0.1:5001/v2

---

## ✅ Verification Checklist

- [x] README.md updated with acknowledgments section
- [x] README.md GitHub links updated to new repository
- [x] V2 web app footer updated (English)
- [x] V1 web app footer updated (Chinese)
- [x] Funding information complete and accurate
- [x] GitHub repository URL correct
- [x] Links open in new tabs
- [x] Visual styling appropriate
- [x] Text readable and well-formatted
- [x] Both web apps tested and verified

---

## 📸 Screenshots

### V2 Web App Footer (English)
![V2 Footer](screenshots/v2_with_acknowledgments.png)
- GitHub open source link
- Full funding acknowledgment
- Copyright notice

### V1 Web App Footer (Chinese)
![V1 Footer](screenshots/index_footer_acknowledgments.png)
- GitHub 开源链接
- 完整资助信息
- 作者信息卡片

---

## 🎓 Academic Compliance

All required elements for academic publication:

✅ **Funding Acknowledgment:**
- HKUST Start-up Fund (R9911)
- Theme-based Research Scheme (T45-205/21-N)
- InnoHK Initiative
- HKUST-DXM Joint Lab (DXM25EG01)

✅ **Open Science:**
- Code publicly available
- Dataset available for research
- MIT License

✅ **Transparency:**
- Clear repository link
- Data access instructions
- Contact information

---

## 🚀 Next Steps (Optional)

### Future Enhancements
1. Add "Cite This Work" section with BibTeX
2. Add GitHub badge with live star count
3. Add DOI badge (when published)
4. Add arXiv badge (if preprint available)
5. Add ORCID iDs for authors

### Maintenance
- Update funding information if new grants added
- Keep GitHub link prominent
- Update copyright year annually
- Add publication citation when available

---

## 📋 Files Modified

| File | Purpose | Language |
|------|---------|----------|
| `README.md` | Main documentation | English |
| `templates/index_v2.html` | Modern web interface | English |
| `templates/index.html` | Classic web interface | Chinese |

---

## 💡 Design Rationale

### Why Prominent GitHub Link?
- Increases visibility and stars
- Encourages community contributions
- Demonstrates open science commitment
- Helps reproducibility

### Why Funding in Footer?
- Standard academic practice
- Grant requirement compliance
- Institutional acknowledgment
- Professional presentation

### Why Both Web Apps?
- V1: Legacy Chinese interface for local users
- V2: Modern English interface for international research
- Ensures all audiences can access information

---

**Status**: ✅ Complete and Deployed

**Last Updated**: January 21, 2026

**Verified By**: Development Team
