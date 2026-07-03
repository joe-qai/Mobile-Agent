import json


def emit_assertion(dimension, name, status, target=None, message=None, severity="major", step_index=None, evidence=None):
    """输出UI兼容性断言事件"""
    event = {
        "type": "assertion",
        "dimension": dimension,
        "name": name,
        "status": status
    }
    if target:
        event["target"] = target
    if message:
        event["message"] = message
    if severity:
        event["severity"] = severity
    if step_index is not None:
        event["step_index"] = step_index
    if evidence:
        event["evidence"] = evidence
    print(json.dumps(event, ensure_ascii=False), flush=True)

def check_element_exists(d, by, value, name, step_index=None):
    """检查元素是否存在 - layout维度断言"""
    try:
        if by == "text":
            element = d(text=value)
        elif by == "resourceId":
            element = d(resourceId=value)
        elif by == "description":
            element = d(description=value)
        else:
            element = d(text=value)
        
        if element.exists:
            emit_assertion("layout", name, "passed", target=f"{by}={value}", 
                          message=f"元素 {value} 存在", step_index=step_index)
            return True
        else:
            emit_assertion("layout", name, "failed", target=f"{by}={value}", 
                          message=f"元素 {value} 不存在", severity="major", step_index=step_index)
            return False
    except Exception as e:
        emit_assertion("layout", name, "failed", target=f"{by}={value}", 
                      message=f"检查元素失败: {str(e)}", severity="major", step_index=step_index)
        return False

def check_text_displayed(d, by, value, expected_text, name, step_index=None):
    """检查文本显示 - text维度断言"""
    try:
        if by == "text":
            element = d(text=value)
        elif by == "resourceId":
            element = d(resourceId=value)
        else:
            element = d(text=value)
        
        if element.exists:
            actual_text = element.get_text()
            if expected_text in actual_text or actual_text in expected_text:
                emit_assertion("text", name, "passed", target=f"{by}={value}", 
                              message=f"文本显示正确: {actual_text}", step_index=step_index)
                return True
            else:
                emit_assertion("text", name, "failed", target=f"{by}={value}", 
                              message=f"文本不匹配，期望: {expected_text}，实际: {actual_text}", 
                              severity="major", step_index=step_index)
                return False
        else:
            emit_assertion("text", name, "failed", target=f"{by}={value}", 
                          message=f"元素不存在", severity="major", step_index=step_index)
            return False
    except Exception as e:
        emit_assertion("text", name, "failed", target=f"{by}={value}", 
                      message=f"检查文本失败: {str(e)}", severity="major", step_index=step_index)
        return False

def check_page_state(d, check_elements, page_name, step_index=None):
    """检查页面状态 - page_state维度断言"""
    for by, value in check_elements:
        try:
            if by == "text":
                element = d(text=value)
            elif by == "resourceId":
                element = d(resourceId=value)
            else:
                element = d(text=value)
            
            if not element.exists:
                emit_assertion("page_state", f"{page_name}页面加载失败", "failed", 
                              target=f"{page_name}.{value}", 
                              message=f"关键元素 {value} 不存在", 
                              severity="blocker", step_index=step_index)
                return False
        except Exception as e:
            emit_assertion("page_state", f"{page_name}页面加载失败", "failed", 
                          target=f"{page_name}", 
                          message=f"检查页面状态失败: {str(e)}", 
                          severity="blocker", step_index=step_index)
            return False
    
    emit_assertion("page_state", f"{page_name}页面加载完成", "passed", 
                  target=page_name, message="所有关键元素均存在", step_index=step_index)
    return True

def check_interaction_response(d, action_name, check_element_by, check_element_value, step_index=None):
    """检查交互响应 - interaction维度断言"""
    try:
        if check_element_by == "text":
            element = d(text=check_element_value)
        elif check_element_by == "resourceId":
            element = d(resourceId=check_element_value)
        else:
            element = d(text=check_element_value)
        
        if element.wait(timeout=5.0):
            emit_assertion("interaction", f"{action_name}操作成功", "passed", 
                          target=check_element_value, 
                          message=f"操作后检测到目标元素 {check_element_value}", 
                          step_index=step_index)
            return True
        else:
            emit_assertion("interaction", f"{action_name}操作失败", "failed", 
                          target=check_element_value, 
                          message=f"操作后未检测到目标元素 {check_element_value}", 
                          severity="blocker", step_index=step_index)
            return False
    except Exception as e:
        emit_assertion("interaction", f"{action_name}操作失败", "failed", 
                      target=check_element_value, 
                      message=f"交互检查失败: {str(e)}", 
                      severity="blocker", step_index=step_index)
        return False
