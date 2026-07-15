package com.foo;

import org.springframework.stereotype.Service;

@Service
public class UserService {
    public String getById(Long id) {
        return "user-" + id;
    }
}
