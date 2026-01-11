"""
更新第一个案例内容，生成问题，并用thinking模式评估
"""
import pandas as pd
import os
from datetime import datetime
import concurrent.futures
from utils.ai_api import ai_api
from utils.evaluator import AnswerEvaluator
from utils.process_cleanup import setup_signal_handlers, SafeThreadPoolExecutor
from config import MAX_CONCURRENT_WORKERS

# 设置信号处理器，确保中断时正确清理
setup_signal_handlers()


def main():
    """主函数"""
    print('=' * 80)
    print('更新第一个案例内容并生成问题，使用Thinking模式评估')
    print('=' * 80)
    print()
    
    # 读取Excel文件
    excel_file = 'data/1.3号案例内容提取_v3_日期补齐_20260103_154847_ID时间补齐_20260103_155150.xlsx'
    
    if not os.path.exists(excel_file):
        print(f"错误：找不到文件 {excel_file}")
        return
    
    print(f"读取文件: {excel_file}")
    df = pd.read_excel(excel_file)
    print(f"总案例数: {len(df)}")
    print()
    
    # 更新第一个案例的内容
    first_case = df.iloc[0]
    case_id = first_case.get('案例ID', '')
    case_title = first_case.get('案例标题', '')
    
    print(f"更新案例: {case_title} (ID: {case_id})")
    print()
    
    # 新的案例内容
    new_case_content = """2018年8月，被告人牟某翰与被害人刘某某（系化名，女，殁年24岁）确立恋爱关系。2018年9月至2019年10月，二人在北京市海淀区某大学的学生公寓，以及牟某翰位于北京市朝阳区的家中、刘某某位于广东省东莞市的家中共同居住。其间，二人购买家居用品布置居所，共同进行家务活动，共同生活。2019年1月至2月，在春节前后，牟某翰、刘某某先后到广东省东莞市、山东省青岛市与双方家长见面。双方的关系已得到彼此父母的认可，二人在微信中互称对方家长为父母。双方的经济往来较为频繁，主要用于双方的生活消费。双方实际处于谈婚论嫁的阶段，为共同组建家庭做准备。
2019年1月起，被告人牟某翰因纠结刘某某以往的性经历，心生不满，多次追问刘某某性经历细节，与刘某某发生争吵，高频次、长时间、持续性辱骂刘某某，并表达过让刘某某通过"打胎"方式以换取其心理平衡等过激言词。同年6月13日，刘某某在与牟某翰争吵后割腕自残。同年8月30日，刘某某在与牟某翰争吵后吞食药物，为此，医院对刘某某采取洗胃等急救措施。之后，刘某某康复。
2019年10月9日中午，刘某某与被告人牟某翰在牟某翰位于北京市朝阳区的家中再次发生争吵，并遭到牟某翰的辱骂。当日15时17分许，刘某某独自外出，入住北京市海淀区某宾馆，并网购药品，服药自杀，被发现后送医抢救。2020年4月11日，刘某某经医治无效死亡。
北京市海淀区人民法院于2023年6月15日作出（2021）京0108刑初382号刑事附带民事判决：被告人牟某翰犯虐待罪，判处有期徒刑三年二个月；被告人牟某翰赔偿附带民事诉讼原告人蔡某某（刘某某的母亲）经济损失人民币七十三万二千六百九十九元五角二分。宣判后，附带民事诉讼原告人蔡某某、被告人牟某翰提出上诉。北京市第一中级人民法院于2023年7月25日作出（2023）京01刑终274号刑事附带民事裁定：驳回上诉，维持原判。"""
    
    # 新的法官判决
    new_judge_decision = """【裁判结果】
法院生效裁判认为，牟某虐待与其共同生活的同居女友，情节恶劣，且致使被害人死亡，其行为已构成虐待罪。牟某与陈某的共同居住等行为构成了实质上的家庭成员关系的共同生活基础事实，二人的男女婚前同居关系应认定为虐待罪中的家庭成员关系，牟某符合虐待罪的犯罪主体要件。从辱骂的言语内容，辱骂行为发生的频次、时长、持续性以及所造成的后果而言，牟某对陈某的辱骂行为已经构成虐待罪中的虐待行为，且达到了情节恶劣的程度。在陈某精神状态不断恶化、不断出现极端行为并最终自杀的进程中，牟某反复实施的高频次、长时间、持续性辱骂行为是制造陈某自杀风险并不断强化、提升风险的决定性因素，因此与陈某自杀身亡这一危害后果具有刑法上的因果关系。综合考虑牟某犯罪的性质、情节、社会危害程度及其认罪态度等因素，对其依法量刑。综上，对牟某以虐待罪，判处有期徒刑三年二个月。
【典型意义】
1.与行为人具有共同生活事实，处于较为稳定的同居状态，形成事实上家庭关系的人，可以认定为刑法第二百六十条第一款规定的"家庭成员"。有共同生活基础事实的婚前同居男女关系中，一方对另一方实施虐待行为，与发生在社会上、单位同事间、邻里间的辱骂、殴打、欺凌，被害人可以躲避、可以向执法机关和司法机关求助不同，受害方往往因"家丑不可外扬"而隐忍，身心常常受到更大伤害，甚至轻生，具有严重的社会危害性。本案中，牟某与陈某之间已经形成了具有上述法律规定的"共同生活的家庭成员"之间的关系。二人的婚前同居关系应认定为虐待罪中的家庭成员关系，牟某符合虐待罪的犯罪主体要件。
2.持续采取凌辱、贬损人格等手段，对家庭成员实施精神摧残、折磨的，属于刑法第二百六十条第一款规定的"虐待"。牟某与陈某共同生活的过程中，相互精神依赖程度不断加深，而牟某始终纠结于陈某过往性经历一事，并认为这是陈某对其亏欠之处，因而心生不满。2019年1月至9月间，牟某高频次、长时间、持续性对陈某进行指责、谩骂、侮辱，言词恶劣、内容粗俗，在日积月累的精神暴力之下，陈某承受了巨大的心理压力，精神上遭受了极度的摧残与折磨，以致实施割腕自残，最终服用药物自杀。牟某的辱骂行为已经构成虐待罪中的虐待行为，且达到了情节恶劣程度。
3.实施精神虐待致使被害人不堪忍受，处于自残、自杀的高风险状态，进而导致被害人自残、自杀的，应当认定虐待行为与危害结果之间存在因果关系。陈某在与牟某确立恋爱关系后，对牟某的精神依赖程度不断加深，牟某长期、日积月累对其侮辱、谩骂，进行精神折磨与打压，贬损其人格，造成陈某在案发时极度脆弱的精神状态。牟某作为陈某精神状态极度脆弱的制造者和与陈某之间具有亲密关系并对陈某负有一定扶助义务的共同生活人员，在陈某已出现割腕自残，以及服用过量药物后进行洗胃治疗并被下发病危通知书的情况下，已经能够明确认识到陈某处于生命的高风险状态，其本应及时关注陈某的精神状况，采取有效措施及时消除上述风险，防止陈某再次出现极端情况。但牟某对由其一手制造的风险状态完全无视，仍然反复指责、辱骂陈某，最终造成陈某不堪忍受，服药自杀身亡，故牟某的虐待行为与陈某自杀身亡的结果之间存在因果关系。

本案的争议焦点有三：一是被告人牟某翰与被害人刘某某之间是否具有"家庭成员"关系；二是牟某翰对刘某某实施的精神摧残、折磨行为是否属于虐待行为；三是牟某翰的行为与刘某某自杀身亡是否存在刑法上的因果关系。
第一，被告人牟某翰与被害人刘某某具有共同生活事实，处于较为稳定的同居状态，形成事实上家庭关系，对牟某翰而言，刘某某属于《中华人民共和国刑法》第二百六十条第一款规定的"家庭成员"。根据刑法第二百六十条第一款的规定，虐待罪的犯罪对象是"家庭成员"，但"家庭成员"的范围未被刑法明确界定。1979年刑法规定虐待罪的行为方式为"虐待家庭成员"，该规定被1997年刑法沿用。基于当时的社会背景，无论是立法机关、司法机关，还是社会公众层面，普遍认为应当将"家庭成员"理解为是传统婚姻家庭关系中的家庭成员。但随着我国经济社会快速发展，大众的思想观念改变巨大、日益多元，社会环境发生深刻变化，男女婚前同居现象日益增多、常见。实践中，发生在婚前同居关系人员之间的暴力犯罪案件时有发生，对被害人人身权利造成严重侵害，也破坏社会和谐稳定。当前，形成共同生活基础事实的婚前同居关系，很多情形下，同样具有传统家庭成员关系的相互依赖、相对稳定等特征。在具有共同生活基础事实的婚前同居关系中，一方对另一方实施虐待行为，与发生在社会上、同事间、邻里间的殴打、欺凌、辱骂，被害人可以躲避、向执法司法机关求助有所不同，被害人出于继续保持同居关系、"家丑不可外扬"等考虑，往往选择隐忍，致使身心遭受持续的、更大的伤害，甚至导致轻生，具有严重社会危害性。基于此，对虐待罪中的"家庭成员"的理解和把握应当与时俱进、适当拓展，作出符合立法精神、符合时代发展、符合社会生活实际、符合人民群众普遍认知的解释。
2016年3月1日施行的《中华人民共和国反家庭暴力法》第三十七条已经规定："家庭成员以外共同生活的人之间实施的暴力行为，参照本法规定执行。"可见，为了适应社会发展实际，为了有效保护传统家庭成员以外共同生活的人的人身权利，反家庭暴力法已经拓展了家庭暴力违法犯罪的规制范围，换一个角度看，实际是拓展了"家庭成员"的范围。《最高人民法院、最高人民检察院、公安部、司法部关于依法办理家庭暴力犯罪案件的意见》（法发〔2015〕4号，以下简称《意见》）也明确，家庭暴力犯罪不仅发生在家庭成员之间，还发生在具有监护、扶养、寄养、同居等关系的共同生活人员之间。在此背景下，就虐待罪的犯罪对象而言，除了传统的家庭成员之外，具有共同生活事实，形成较为稳定的同居关系的事实家庭成员，也应纳入其中。本案中，被告人牟某翰与被害人刘某某之间已形成事实家庭成员关系。具体而言：（1）双方恋爱交往是为了共同组建家庭，主观上具有共同生活的目的；从共同生活的事实，以及双方经济往来支出等情况来看，二人已具备了较为稳定的共同生活关系，且精神上相互依赖，经济上相互融通。（2）双方在重要节假日共同居住于对方家中共度节日，双方家长对二人予以认可，分别以准女婿、准儿媳的态度对待二人，并时常谈起组建家庭的相关事宜。因此，结合社会公众的一般观念，应当认定牟某翰与刘某某之间的婚前同居关系已形成事实家庭成员关系。第二，被告人牟某翰持续性地采取凌辱、贬损人格等手段，对被害人刘某某实施精神摧残、折磨，属于刑法第二百六十条规定的"虐待"。实践中，虐待罪多表现为，行为人采用殴打、冻饿、强迫过度劳动、限制人身自由等手段，对家庭成员的身体进行摧残、折磨。但是，刑法并未将虐待行为仅限于身体虐待。长期、反复对家庭成员实施精神摧残、折磨，造成被害人的精神极度痛苦的，也应当认定为虐待。对此，《意见》明确，对家庭成员进行精神摧残、折磨的，亦应当认定为虐待罪中的虐待行为。
本案中，被告人牟某翰与被害人刘某某确立恋爱关系、婚前同居后，在共同生活过程中，本应平等互待，理性平和处理二人之间的矛盾，友善协商解决存在的情感问题。但牟某翰却出于偏执心理，不能正确对待刘某某以往的性经历，高频次、持续性凌辱刘某某，言词恶劣、内容粗鄙，对刘某某进行精神折磨，严重贬损其人格。刘某某不愿与牟某翰分手，但又不知如何面对牟某翰反复持续施加的精神虐待，以致数次自残、自杀。根据案件事实，应当认定牟某翰所实施的行为属于虐待行为。
第三，被告人牟某翰长期对被害人刘某某实施精神虐待，导致刘某某不堪忍受而自杀，具有刑法上的因果关系。刑法第二百六十条第二款规定，虐待"致使被害人重伤、死亡的"，应当以虐待罪论处，加重刑罚。适用该规定，必须以虐待行为与重伤、死亡结果之间存在刑法上的因果关系为条件。从虐待罪的特点看，被害人重伤、死亡的结果，既有可能是虐待行为直接导致的，如行为人长期、多次实施虐待行为，逐步累积造成被害人身体损害，进而导致重伤或者死亡；也有可能是间接的，如被害人因为遭受虐待，不堪忍受自残、自杀，进而导致重伤或者死亡。对于后者，无论是从事物发展的自然规律，还是从社会公众的一般观念看，都不应当否定虐待行为与伤亡结果之间的因果关系。本案中，随着被告人牟某翰与被害人刘某某恋爱关系、同居生活的发展，刘某某对牟某翰的精神依赖程度不断加深。牟某翰的精神虐待行为，致使对其具有高度精神依赖的刘某某精神状态不断恶化，逐渐将刘某某推向精神崩溃的地步。特别是，牟某翰在刘某某因不堪忍受凌辱而出现过割腕自残、大量吞服安眠药等轻生行为的情况下，明知刘某某已极度脆弱，遭遇不良刺激后随时可能再度轻生，仍然无视其所造成的高风险状态，最终导致刘某某不堪忍受、服药自杀身亡。显然，牟某翰实施的精神虐待行为是导致刘某某自杀身亡的决定性因素，应当认定二者之间具有刑法上的因果关系。
综上，根据刑法和相关法律规定，基于本案事实、证据，法院依法认定被告人牟某翰凌辱同居女友致其自杀的行为构成虐待罪，并综合考虑犯罪的事实、性质、情节和社会危害程度，对其判处有期徒刑三年二个月。
裁判要旨
1.与行为人具有共同生活事实，处于较为稳定的同居状态，形成事实上家庭关系的人，可以认定为刑法第二百六十条第一款规定的"家庭成员"。
2.持续采取凌辱、贬损人格等手段，对家庭成员实施精神摧残、折磨的，属于刑法第二百六十条第一款规定的"虐待"。
3.实施精神虐待致使被害人不堪忍受，处于自残、自杀的高风险状态，进而导致被害人自残、自杀的，应当认定虐待行为与危害结果之间存在因果关系
《中华人民共和国刑法》第260条"""
    
    # 更新第一个案例
    df.at[0, '案例内容'] = new_case_content
    df.at[0, '法官判决'] = new_judge_decision
    
    print("✓ 案例内容已更新")
    print()
    
    # 1. 脱敏处理
    print("=" * 80)
    print("步骤1: 脱敏处理")
    print("=" * 80)
    print()
    
    from utils.data_masking import DataMaskerAPI
    masker = DataMaskerAPI()
    
    case_dict = {
        'title': case_title,
        'case_text': new_case_content,
        'judge_decision': new_judge_decision
    }
    
    print("→ 进行脱敏处理...")
    masked_case = masker.mask_case_with_api(case_dict)
    
    masked_title = masked_case.get('title_masked', '')
    masked_content = masked_case.get('case_text_masked', '')
    masked_judge = masked_case.get('judge_decision_masked', '')
    
    print("✓ 脱敏完成")
    print()
    
    # 2. 生成问题（不再提取法官回答）
    print("=" * 80)
    print("步骤2: 生成5个问题（Thinking模式）")
    print("=" * 80)
    print()
    
    print("→ 生成问题...")
    questions = ai_api.generate_questions(masked_content, num_questions=5)
    
    print(f"✓ 问题生成完成（共{len(questions)}个）")
    print()
    
    # 显示生成的问题
    for i, question in enumerate(questions, 1):
        print(f"问题{i}: {question[:80]}...")
    print()
    
    # 3. 生成AI回答并评估（使用thinking模式，并发处理）
    print("=" * 80)
    print("步骤3: 生成AI回答并评估（Thinking模式，并发处理）")
    print("=" * 80)
    print()
    
    def process_single_question(question, q_num):
        """处理单个问题：生成AI回答并评估（直接与整个法官判决对比）"""
        import time
        start_time = time.time()
        print(f'[问题{q_num}/5] 开始处理...')
        print(f'[问题{q_num}/5] 问题: {question[:60]}...')
        
        result = {
            '案例ID': case_id,
            '案例标题': case_title,
            '案例标题（脱敏）': masked_title,
            '问题编号': q_num,
            '问题': question
        }
        
        try:
            # 生成AI回答（使用thinking模式）
            print(f'[问题{q_num}/5] → 步骤1/2: 生成AI回答（Thinking模式）...')
            ai_response = ai_api.analyze_case(masked_content, question=question, use_thinking=True)
            
            if isinstance(ai_response, dict):
                ai_answer = ai_response.get('answer', '')
                ai_thinking = ai_response.get('thinking', '')
            else:
                ai_answer = ai_response
                ai_thinking = ''
            
            result['AI回答'] = ai_answer
            if ai_thinking:
                result['AI回答Thinking'] = ai_thinking
                print(f'[问题{q_num}/5] ✓ 步骤1/2完成: AI回答（答案：{len(ai_answer)}字符，Thinking：{len(ai_thinking)}字符）')
            else:
                print(f'[问题{q_num}/5] ✓ 步骤1/2完成: AI回答（{len(ai_answer)}字符）')
            
            # 进行评分（使用thinking模式，直接与整个法官判决对比）
            print(f'[问题{q_num}/5] → 步骤2/2: 进行评分（Thinking模式，与整个法官判决对比）...')
            evaluator = AnswerEvaluator()
            evaluation = evaluator.evaluate_answer(
                ai_answer=ai_answer,
                judge_decision=masked_judge,  # 使用整个法官判决作为对比标准
                question=question,
                case_text=masked_content
            )
            
            result['总分'] = evaluation['总分']
            result['百分制'] = evaluation['百分制']
            result['分档'] = evaluation['分档']
            
            # 添加各维度得分
            for dimension, score in evaluation['各维度得分'].items():
                result[f'{dimension}_得分'] = score
            
            result['详细评价'] = evaluation['详细评价']
            
            # 评价Thinking内容（如果存在）
            if '评价Thinking' in evaluation:
                result['评价Thinking'] = evaluation['评价Thinking']
            
            # 错误标记（按级别分类）
            error_mark = evaluation.get('错误标记', '')
            error_details = evaluation.get('错误详情', {})
            result['错误标记'] = error_mark
            if error_mark:
                print(f'[问题{q_num}/5] ⚠️ 检测到错误: {error_mark}')
            # 保留错误详情（如果需要）
            if error_details:
                if error_details.get('微小错误'):
                    result['微小错误'] = '; '.join(error_details['微小错误'])
                if error_details.get('明显错误'):
                    result['明显错误'] = '; '.join(error_details['明显错误'])
                if error_details.get('重大错误'):
                    result['重大错误'] = '; '.join(error_details['重大错误'])
            
            total_score_val = evaluation["总分"]
            percentage_val = evaluation["百分制"]
            elapsed_time = time.time() - start_time
            print(f'[问题{q_num}/5] ✓ 完成（总分：{total_score_val}/20，百分制：{percentage_val}，耗时：{elapsed_time:.1f}秒）')
            print()
            
            return result
            
        except Exception as e:
            elapsed_time = time.time() - start_time
            print(f'[问题{q_num}/5] ✗ 处理失败: {str(e)}（耗时：{elapsed_time:.1f}秒）')
            print()
            result['处理错误'] = str(e)
            return result
    
    # 并发处理所有问题
    results = []
    max_workers = min(MAX_CONCURRENT_WORKERS, len(questions))
    total_questions = len(questions)
    print(f"使用 {max_workers} 个并发线程处理 {total_questions} 个问题")
    print()
    
    completed_count = 0
    with SafeThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_q = {
            executor.submit(process_single_question, question, i+1): (i, question)
            for i, question in enumerate(questions)
        }
        
        for future in concurrent.futures.as_completed(future_to_q):
            index, question = future_to_q[future]
            try:
                result = future.result()
                if result:
                    results.append(result)
                    completed_count += 1
                    progress = (completed_count / total_questions) * 100
                    print(f"[总体进度] {completed_count}/{total_questions} 个问题已完成 ({progress:.1f}%)")
                    print()
            except Exception as e:
                completed_count += 1
                print(f"✗ 问题{index+1} 处理异常: {str(e)}")
                print(f"[总体进度] {completed_count}/{total_questions} 个问题已完成")
                print()
    
    # 按问题编号排序
    results.sort(key=lambda x: x.get('问题编号', 0))
    
    # 构建结果DataFrame
    result_df = pd.DataFrame(results)
    
    # 重新排列列的顺序
    columns_order = [
        '案例ID', '案例标题', '案例标题（脱敏）', '问题编号', '问题', 
        'AI回答', 'AI回答Thinking',
        '总分', '百分制', '分档',
        '规范依据相关性_得分', '涵摄链条对齐度_得分',
        '价值衡量与同理心对齐度_得分', '关键事实与争点覆盖度_得分',
        '裁判结论与救济配置一致性_得分',
        '错误标记', '微小错误', '明显错误', '重大错误',
        '详细评价', '评价Thinking', '处理错误'
    ]
    
    # 只包含存在的列
    final_columns = [col for col in columns_order if col in result_df.columns]
    # 添加其他列
    other_columns = [col for col in result_df.columns if col not in final_columns]
    final_columns.extend(other_columns)
    
    result_df = result_df[final_columns]
    
    # 保存结果
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_file = f'data/更新案例_评估结果_{timestamp}.xlsx'
    
    print()
    print('=' * 80)
    print('保存结果...')
    print('=' * 80)
    print(f"保存到: {output_file}")
    
    result_df.to_excel(output_file, index=False)
    print("✓ 保存完成！")
    print()
    
    # 显示统计信息
    print('=' * 80)
    print('处理统计:')
    print('=' * 80)
    print(f"总问题数: {len(result_df)}")
    
    if '总分' in result_df.columns:
        avg_score = result_df['总分'].mean()
        avg_percentage = result_df['百分制'].mean()
        print(f"平均总分: {avg_score:.2f}/20")
        print(f"平均百分制: {avg_percentage:.2f}")
        print(f"最高分: {result_df['总分'].max():.2f}/20 ({result_df['百分制'].max():.2f}分)")
        print(f"最低分: {result_df['总分'].min():.2f}/20 ({result_df['百分制'].min():.2f}分)")
    
    print()
    print('=' * 80)
    print('✓ 处理完成！')
    print('=' * 80)


if __name__ == '__main__':
    main()

