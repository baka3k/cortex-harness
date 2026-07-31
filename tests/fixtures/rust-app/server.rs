pub mod network {
    use std::collections::HashMap;

    pub struct Connection {
        pub host: String,
        pub port: u32,
    }

    impl Connection {
        pub fn connect(&self) -> bool {
            true
        }
    }

    pub fn create_connection(host: &str) -> Connection {
        Connection {
            host: host.to_string(),
            port: 8080,
        }
    }
}

struct Server {
    connections: Vec<network::Connection>,
}

impl Server {
    fn start(&self) {
        let conn = network::create_connection("localhost");
        conn.connect();
    }
}
