package com.bank.chat.assign.domain;

import javax.persistence.Column;
import javax.persistence.Entity;
import javax.persistence.Id;
import javax.persistence.Table;
import java.util.UUID;

@Entity
@Table(name = "assign_split")
public class AssignSplitEntity {

    @Id
    @Column(columnDefinition = "uuid", nullable = false)
    private UUID id;

    @Column(nullable = false, unique = true, length = 64)
    private String name;

    public UUID getId() {
        return id;
    }

    public void setId(UUID id) {
        this.id = id;
    }

    public String getName() {
        return name;
    }

    public void setName(String name) {
        this.name = name;
    }
}
