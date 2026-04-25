package com.bank.chat.assign.web.dto;

import javax.validation.constraints.NotBlank;
import java.util.List;

public class OpenSessionBody {

    @NotBlank
    private String operatorId;

    private List<String> splitNames;

    public String getOperatorId() {
        return operatorId;
    }

    public void setOperatorId(String operatorId) {
        this.operatorId = operatorId;
    }

    public List<String> getSplitNames() {
        return splitNames;
    }

    public void setSplitNames(List<String> splitNames) {
        this.splitNames = splitNames;
    }
}
